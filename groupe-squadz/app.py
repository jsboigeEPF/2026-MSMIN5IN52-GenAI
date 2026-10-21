"""Interface Streamlit de la Fabrique de Schemas Pedagogiques."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import streamlit as st

GROUP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = GROUP_DIR / "data" / "outputs" / "system"
CUSTOM_CONCEPT_ID = "custom"

if str(GROUP_DIR) not in sys.path:
    sys.path.insert(0, str(GROUP_DIR))

from src.config import CONCEPTS_PEDAGOGIQUES  # noqa: E402
from src.evaluator import evaluer_lisibilite_image  # noqa: E402
from src.generator import generer_et_sauvegarder_image  # noqa: E402


def slugify(value: str) -> str:
    """Cree un nom de fichier stable et lisible pour un concept personnalise."""
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized[:60] or "concept_personnalise"


def concept_selectionne(selection: str, personnalise: str) -> dict[str, Any] | None:
    """Retourne le concept choisi dans le format attendu par le generateur."""
    if selection == CUSTOM_CONCEPT_ID:
        titre = personnalise.strip()
        if not titre:
            return None
        return {
            "id": CUSTOM_CONCEPT_ID,
            "titre": titre,
            "description": f"Schema pedagogique expliquant clairement {titre}.",
        }
    return next(
        (concept for concept in CONCEPTS_PEDAGOGIQUES if concept["titre"] == selection),
        None,
    )


def output_path_for(concept: dict[str, Any]) -> Path:
    """Determine le fichier de sortie sans dependre du repertoire courant."""
    filename = (
        f"concept_{concept['id']}.png"
        if concept["id"] != CUSTOM_CONCEPT_ID
        else f"custom_{slugify(concept['titre'])}.png"
    )
    return OUTPUT_DIR / filename


@st.cache_data(show_spinner=False)
def evaluate_image(
    image_path: str, modified_at: float, expected_keywords: tuple[str, ...]
) -> dict[str, Any]:
    """Évalue l'image une fois par version de fichier pour garder l'UI fluide."""
    del modified_at
    return evaluer_lisibilite_image(image_path, list(expected_keywords))


def source_label(source: str) -> str:
    """Traduit le code technique du générateur en libellé lisible."""
    return {
        "cache": "Cache local / données pré-générées",
        "plan_a": "Plan A — OpenRouter / Qwen",
        "plan_b": "Plan B — Groq + Pillow (secours)",
    }.get(source, "Génération résiliente")


def render_app() -> None:
    st.set_page_config(
        page_title="Fabrique de Schemas",
        page_icon="📚",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        .block-container { max-width: 1180px; padding-top: 2rem; }
        .hero { padding: 1.4rem 1.6rem; border-radius: 18px;
                background: linear-gradient(120deg, #172554, #2563eb); color: white; }
        .hero h1 { margin: 0; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="hero"><h1>📚 Fabrique de Schémas Pédagogiques</h1>'
        "<p>Un support clair, généré avec une architecture résiliente.</p></div>",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Choisir un concept")
        options = [concept["titre"] for concept in CONCEPTS_PEDAGOGIQUES]
        options.append("✦ Concept personnalisé")
        selection = st.selectbox("Concept du MVP", options)
        personnalise = ""
        if selection == "✦ Concept personnalisé":
            personnalise = st.text_input(
                "Votre concept",
                placeholder="Ex. La méthode scientifique",
            )
        regenerate = st.button(
            "↻ Générer / Régénérer",
            type="primary",
            use_container_width=True,
        )

    concept = concept_selectionne(
        CUSTOM_CONCEPT_ID if selection == "✦ Concept personnalisé" else selection,
        personnalise,
    )
    if concept is None:
        st.info("Saisissez un concept personnalisé pour commencer.")
        return

    output_path = output_path_for(concept)
    cached = output_path.exists()
    generation_source = "cache" if cached and not regenerate else ""
    st.subheader(concept["titre"])
    st.caption(concept["description"])

    if cached and not regenerate:
        st.success("✓ Schéma disponible — affiché depuis le cache / les données pré-générées.")
    elif regenerate:
        with st.status("Génération en cours (Plan A → Plan B → secours local)…", expanded=True) as status:
            try:
                result = generer_et_sauvegarder_image(
                    concept,
                    str(output_path),
                    baseline=False,
                    force_regenerate=True,
                )
            except (OSError, ValueError, KeyError, RuntimeError) as error:
                status.update(label="Échec de la génération", state="error")
                st.error(f"La génération a échoué : {error}")
                return
            status.update(label=f"Génération terminée ({result})", state="complete")
            st.success("✓ Le schéma a été créé ou mis à jour.")
            generation_source = result if isinstance(result, str) else "plan_b"

    if not output_path.exists():
        st.warning("Aucun schéma disponible. Lancez une génération.")
        return

    image_column, summary_column = st.columns([1.35, 1])
    with image_column:
        st.image(str(output_path), caption=concept["titre"], use_container_width=True)
        with output_path.open("rb") as image_file:
            image_bytes = image_file.read()
        st.download_button(
            "⬇ Télécharger le schéma PNG",
            data=image_bytes,
            file_name=output_path.name,
            mime="image/png",
            use_container_width=True,
        )
    with summary_column:
        st.markdown("### Résumé pédagogique")
        st.write(concept["description"])
        st.markdown(
            "Le schéma présente la définition, les points clés et une mise en "
            "situation pour faciliter la mémorisation."
        )
        st.markdown("### Qualité et provenance")
        try:
            expected_keywords = (
                concept["titre"].split()[0],
                "définition",
                "exemple",
            )
            report = evaluate_image(
                str(output_path),
                output_path.stat().st_mtime,
                expected_keywords,
            )
        except (OSError, ValueError, RuntimeError) as error:
            st.error(f"Évaluation OCR indisponible : {error}")
        else:
            score = float(report.get("score", 0))
            if report.get("status") == "VALID":
                st.success(f"Lisibilité OCR : {score:.1f} % — bonne lisibilité")
            else:
                st.warning(f"Lisibilité OCR : {score:.1f} % — à vérifier")
            st.caption(f"Source : {source_label(generation_source)}")


if __name__ == "__main__":
    render_app()
