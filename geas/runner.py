"""
geas.runner — ExecutionRunner: ejecución de un ticket por un agente (§27).

Une el ciclo completo que GEAS registra y el Harness controla:

    GEAS ──► Execution ──► Harness ──► Agent ──► Worktree ──► Tests ──► Commit

El runner NO decide si el agente puede ejecutar el ticket: eso lo decide
GEAS (estado del ticket, permisos del actor). El runner materializa
esa autorización en una ejecución real, aislada y trazada:

    1. valida el ticket y registra una Execution (model traceability §28)
    2. prepara un worktree aislado con la branch del ticket (§19)
    3. ejecuta el comando del agente dentro del worktree
    4. opcionalmente ejecuta un comando de tests en el MISMO worktree y
       registra un TestResult (§20)
    5. registra eventos AGENT_STARTED / AGENT_STOPPED / TEST_* (§30)
    6. limpia el worktree (o lo conserva con --keep)

No gestiona secretos ni credenciales: esa responsabilidad pertenece al
proveedor de runtime real (contenedor, sandbox, CI).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from geas.harness import LocalHarness, ProcessResult, WorktreeEnvironment
from geas.llm import get_client
from geas.models import Agent, Event, Execution, TestResult, TicketStatus
from geas.storage import Storage


@dataclass
class RunnerConfig:
    """Configuración de una ejecución."""

    command: list[str]
    test_command: list[str] | None = None
    worktrees_root: str | Path = ".geas/worktrees"
    keep_worktree: bool = False
    timeout: int | None = None
    actor_id: str = "runner"
    tools_used: list[str] = field(default_factory=list)
    # Métricas opcionales del modelo (tokens/coste) — las rellena el runtime
    # que invoca al agente, no el runner.
    tokens_input: int = 0
    tokens_output: int = 0
    cost: float = 0.0
    # Modo multi-provider (§43): si llm_provider está, el comando local se
    # sustituye por una llamada real al LLM y la respuesta se escribe en el
    # worktree (llm_output_file). Los tokens/coste reales vuelven en la
    # respuesta y se registran en la Execution (update_execution_usage).
    llm_provider: str | None = None
    llm_model: str = ""
    llm_prompt: str = ""
    llm_max_tokens: int = 2048
    llm_output_file: str = "GEAS_ANSWER.md"
    llm_api_base: str | None = None


@dataclass
class RunSummary:
    """Resultado resumido de una ejecución."""

    execution_id: str
    ticket_id: str
    branch: str
    returncode: int
    result: str
    test_status: str | None = None
    stdout: str = ""
    stderr: str = ""
    worktree: str = ""
    kept_worktree: bool = False


class ExecutionRunner:
    """Ejecuta un ticket aislado en un worktree y lo deja trazado."""

    def __init__(self, storage: Storage, harness: LocalHarness | None = None):
        self.storage = storage
        self.harness = harness or LocalHarness(".geas/worktrees")

    # ─── Ciclo principal ─────────────────────────────────────────────────

    def run_ticket(
        self,
        ticket_id: str,
        config: RunnerConfig,
        repository: str | Path,
        agent: Agent | None = None,
    ) -> RunSummary:
        """Ejecuta el ticket `ticket_id` en un worktree aislado de `repository`.

        El ticket debe existir y estar en FREE, CLAIMED o IN_PROGRESS: el
        runner reclama los que están libres igual que haría `work start`,
        y rechaza los que ya están en BLOCKED/REVIEW/DONE/CANCELLED.
        """
        ticket = self.storage.get_ticket(ticket_id)
        if ticket is None:
            raise ValueError(f"Ticket no encontrado: {ticket_id}")

        allowed = {
            TicketStatus.FREE,
            TicketStatus.CLAIMED,
            TicketStatus.IN_PROGRESS,
        }
        if ticket.status not in allowed:
            raise ValueError(
                f"Ticket {ticket_id} en estado {ticket.status.value}; "
                "no se puede ejecutar"
            )

        # Reclamar tickets libres (igual que work start) — claim atómico §39
        if ticket.status is TicketStatus.FREE:
            self.storage.start_ticket(
                ticket.id, branch=ticket.branch, actor_id=config.actor_id
            )

        branch = ticket.branch or f"geas/{ticket.id}"

        # Multi-provider (§43): la Execution registra el proveedor REAL que
        # ejecuta el ticket — el del agente, o el LLM del config si el runner
        # está en modo LLM (que sustituye al comando local).
        execution = Execution(
            ticket_id=ticket.id,
            actor_id=config.actor_id,
            harness_id="local",
            provider=(
                config.llm_provider
                if config.llm_provider
                else (agent.provider if agent else "local")
            ),
            model=(
                config.llm_model
                if config.llm_provider
                else (agent.model if agent else "cli")
            ),
            model_version=agent.model_version if agent else "",
            tokens_input=config.tokens_input,
            tokens_output=config.tokens_output,
            cost=config.cost,
            tools_used=config.tools_used,
        )
        self.storage.create_execution(execution)

        self._event(
            "AGENT_STARTED",
            ticket,
            metadata={
                "execution_id": execution.id,
                "agent": agent.name if agent else "",
            },
            actor_id=config.actor_id,
        )

        # Worktree aislado: aquí trabaja el agente y aquí corren sus tests
        env = self.harness.prepare(ticket.id, repository, branch)
        test_status = None
        try:
            if config.llm_provider:
                process = self._run_llm(
                    ticket, env, config, agent, execution.id
                )
            else:
                process = self.harness.run(env, config.command, timeout=config.timeout)
            if config.test_command:
                test_status = self._run_tests(ticket, env, config)
        except Exception:
            # Nunca dejar worktrees huérfanos si el arranque falla
            if not config.keep_worktree:
                self.harness.cleanup(env)
            raise

        result = "success" if process.returncode == 0 else "failure"
        self.storage.update_execution_result(
            execution.id, result, finished_at=process.finished_at
        )
        self._event(
            "AGENT_STOPPED",
            ticket,
            metadata={
                "execution_id": execution.id,
                "result": result,
                "returncode": process.returncode,
            },
            actor_id=config.actor_id,
        )

        kept = False
        if config.keep_worktree:
            kept = True
        else:
            self.harness.cleanup(env)

        return RunSummary(
            execution_id=execution.id,
            ticket_id=ticket.id,
            branch=branch,
            returncode=process.returncode,
            result=result,
            test_status=test_status,
            stdout=process.stdout,
            stderr=process.stderr,
            worktree=str(env.worktree),
            kept_worktree=kept,
        )

    # ─── Pasos internos ──────────────────────────────────────────────────

    def _run_llm(
        self,
        ticket,
        env: WorktreeEnvironment,
        config: RunnerConfig,
        agent: Agent | None,
        execution_id: str,
    ) -> ProcessResult:
        """Sustituye el comando local por una llamada real al LLM (§43).

        - construye el prompt a partir del ticket (o usa config.llm_prompt)
        - llama al proveedor (openai/anthropic) vía geas.llm
        - escribe la respuesta en el worktree (llm_output_file) para que
          quede como artefacto del ticket
        - registra los tokens/coste reales en la Execution (§28)
        Devuelve un ProcessResult sintético para reutilizar el resto del
        ciclo (tests, eventos, RunSummary).
        """
        system = (
            "Eres un agente de ingeniería trabajando en un ticket de GEAS. "
            "Responde con un plan de implementación concreto y, si procede, "
            "con el código o el diff necesario. Sé conciso y ejecutable."
        )
        if config.llm_prompt:
            user = config.llm_prompt
        else:
            deps = ", ".join(ticket.dependencies) or "ninguna"
            resources = ", ".join(ticket.resources) or "ninguno"
            user = (
                f"Ticket: {ticket.id}\n"
                f"Título: {ticket.title}\n"
                f"Descripción: {ticket.description or '(sin descripción)'}\n"
                f"Prioridad: {ticket.priority}\n"
                f"Dependencias: {deps}\n"
                f"Recursos: {resources}\n"
                f"Branch: {env.branch}\n"
                f"Actor: {config.actor_id}\n\n"
                "Implementa este ticket. Deja el resultado en el repositorio "
                "y explica qué has cambiado."
            )

        started_at = datetime.now(UTC).isoformat()
        client = get_client(
            config.llm_provider or "",
            api_base=config.llm_api_base,
            model=config.llm_model,
        )
        result = client.complete(
            system, user, model=config.llm_model or None, max_tokens=config.llm_max_tokens
        )
        finished_at = datetime.now(UTC).isoformat()

        # Artefacto: la respuesta del LLM queda en el worktree del ticket
        output = Path(env.worktree) / config.llm_output_file
        output.write_text(
            f"# {config.llm_provider} — {result.model}\n\n{result.text}\n",
            encoding="utf-8",
        )

        self.storage.update_execution_usage(
            execution_id,
            tokens_input=result.tokens_input,
            tokens_output=result.tokens_output,
            cost=result.cost,
        )
        return ProcessResult(
            returncode=0 if result.text else 1,
            stdout=result.text,
            stderr="",
            started_at=started_at,
            finished_at=finished_at,
        )

    def _run_tests(self, ticket, env: WorktreeEnvironment, config: RunnerConfig) -> str:
        """Ejecuta el comando de tests en el worktree del ticket (§20)."""
        test_command = config.test_command or []
        if not test_command:
            return ""

        self._event("TEST_STARTED", ticket)
        started_at = datetime.now(UTC).isoformat()
        process = self.harness.run(env, test_command, timeout=config.timeout)
        status = "passed" if process.returncode == 0 else "failed"

        self.storage.create_test_result(
            TestResult(
                ticket_id=ticket.id,
                status=status,
                started_at=started_at,
                finished_at=process.finished_at,
                logs_reference=(
                    process.stdout[-500:] if process.stdout else process.stderr[:500]
                ),
            )
        )
        self._event(
            "TEST_PASSED" if status == "passed" else "TEST_FAILED",
            ticket,
            metadata={"returncode": process.returncode},
        )
        return status

    def _event(
        self,
        event_type: str,
        ticket,
        metadata: dict | None = None,
        actor_id: str = "runner",
    ) -> None:
        self.storage.create_event(
            Event(
                event_type=event_type,
                organization_id=ticket.organization_id,
                actor_id=actor_id,
                resource_type="ticket",
                resource_id=ticket.id,
                metadata=metadata or {},
            )
        )
