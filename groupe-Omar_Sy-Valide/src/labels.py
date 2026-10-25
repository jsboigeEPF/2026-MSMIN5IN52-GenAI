"""French wording shared by the interactive agent and the dashboard: the same words for the same things."""

from src.models.state import AgentProcessState, ProcessStatus
from src.process.graph import Step

STEP_LABELS = {
    Step.PLAN: "Planification",
    Step.PLAN_APPROVAL: "Validation du plan",
    Step.CODE: "Génération du code",
    Step.WRITE_APPROVAL: "Validation de l'écriture",
    Step.VERIFY: "Tests et lint",
    Step.FINALIZE: "Écriture du fichier",
}

STATUS_LABELS = {
    ProcessStatus.INITIALIZED: "Démarrage",
    ProcessStatus.PLANNING: "Planification en cours",
    ProcessStatus.CODING: "Génération du code en cours",
    ProcessStatus.VERIFYING: "Tests en cours",
    ProcessStatus.AWAITING_APPROVAL: "En attente de ta décision",
    ProcessStatus.FINALIZING: "Écriture en cours",
    ProcessStatus.COMPLETED: "Terminé",
    ProcessStatus.REJECTED: "Refusé",
    ProcessStatus.EXPIRED: "Expiré",
    ProcessStatus.FAILED: "Échoué",
}

# Short reason (TUI) and what it means for the user (dashboard).
TERMINAL_REASONS = {
    "human_rejected": "refusé",
    "max_iterations": "trop de demandes d'affinage",
    "approval_expired": "validation expirée",
    "fixes_exhausted": "les tests échouent encore après les corrections automatiques",  # runs before v2
    "write_failed": "écriture refusée",
    "budget_exceeded": "budget dépassé",
    "invalid_llm_output": "réponse du modèle invalide",
    "sandbox_unavailable": "exécution des tests impossible",
    "llm_error": "erreur du modèle",
    "written_despite_failing_tests": "écrit alors que les tests échouent",
}
REASON_EXPLANATIONS = {
    "human_rejected": "Tu as refusé la proposition. Rien n'a été modifié dans ton dossier.",
    "max_iterations": "Trop d'affinages (MAX_REVISIONS). Relance une tâche plus précise.",
    "approval_expired": "Personne n'a répondu avant l'échéance (APPROVAL_TIMEOUT_SEC). Rien n'a été modifié.",
    "fixes_exhausted": "Les tests échouaient après les corrections automatiques. Rien n'a été modifié.",
    "write_failed": "Le fichier a changé dans ton dossier pendant le run : rien n'a été écrasé.",
    "budget_exceeded": "Le coût du run a dépassé MAX_COST_EUR_PER_RUN. Rien n'a été modifié.",
    "invalid_llm_output": "Réponse du modèle inutilisable deux fois de suite. Réessaie ou change de modèle.",
    "sandbox_unavailable": "Les tests n'ont pas pu être lancés (voir SANDBOX_MODE). Rien n'a été modifié.",
    "llm_error": "Le fournisseur du modèle a renvoyé une erreur. Rien n'a été modifié.",
    "written_despite_failing_tests": "Tu as choisi d'écrire le fichier alors que les tests échouaient.",
}

GATE_LABELS = {"plan": "le plan", "write": "l'écriture"}
ACTION_LABELS = {
    "approve": "a validé",
    "revise": "a demandé d'affiner",
    "reject": "a refusé",
    "write_anyway": "a choisi d'écrire quand même",
}

# The transition labels of graph.py, as shown on the dashboard's graph.
EDGE_LABELS = {
    "plan ready": "plan prêt",
    "approved": "validé",
    "approved: write in the worktree and test": "validé : écrire et tester",
    "write anyway (tests fail)": "écrire quand même",
    "refine (revision_iteration < MAX_REVISIONS)": "affiner",
    "revisions exhausted": "trop d'affinages",
    "rejected": "refusé",
    "expired": "expiré",
    "diff ready, nothing written": "diff prêt, rien d'écrit",
    "tests and lint pass": "tests OK",
    "auto-fix (fix_iteration < MAX_FIX)": "correction automatique",
    "fixes exhausted: the human decides": "corrections épuisées : à toi",
    "file written, not committed": "fichier écrit",
    "written although tests fail": "écrit malgré les tests",
    "write refused": "écriture refusée",
}


def status_label(state: AgentProcessState) -> str:
    if state.undone:
        return "Annulé (fichier restauré)"
    if state.terminal_reason == "written_despite_failing_tests":
        return "Terminé (tests en échec)"
    return STATUS_LABELS[state.status]
