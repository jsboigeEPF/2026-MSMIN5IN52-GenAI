"""Tests du contrat et des limites de server.py, via une vraie session MCP en mémoire.

Chaque test reçoit un sandbox temporaire neuf ; le sandbox/ du projet n'est jamais touché.
Lancement depuis la racine du projet : python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp.shared.memory import create_connected_server_and_client_session

import server

logging.getLogger("mcp").setLevel(logging.WARNING)  # masque une ligne INFO par requête MCP

MODIFICATIONS = {
    "write_file": {"path": "nouveau.txt", "content": "x", "mode": "overwrite"},
    "create_directory": {"path": "nouveau_dossier"},
    "move_file": {"source": "data/a.txt", "destination": "b.txt"},
}


def requete_mcp(methode: str, *args):
    """Ouvre une session client MCP sur le serveur et exécute une requête."""

    async def executer():
        async with create_connected_server_and_client_session(server.mcp) as session:
            return await getattr(session, methode)(*args)

    return asyncio.run(executer())


def appel(outil: str, **arguments) -> tuple[bool, str]:
    resultat = requete_mcp("call_tool", outil, arguments)
    return resultat.isError, "\n".join(item.text for item in resultat.content if item.type == "text")


def lire_ressource(uri: str) -> str:
    return requete_mcp("read_resource", uri).contents[0].text


class TestAvecSandbox(unittest.TestCase):
    def setUp(self):
        dossier_temporaire = tempfile.TemporaryDirectory()
        self.addCleanup(dossier_temporaire.cleanup)
        self.base = Path(dossier_temporaire.name).resolve()
        self.racine = self.base / "sandbox"
        (self.racine / "data").mkdir(parents=True)
        (self.racine / "data" / "a.txt").write_text("alpha\n", encoding="utf-8")
        self.dehors = self.base / "dehors.txt"
        self.dehors.write_text("SECRET_HORS_SANDBOX", encoding="utf-8")
        self.fichier_journal = self.racine / ".mcp_audit.jsonl"
        for nom, valeur in {"ROOT": self.racine, "AUDIT_FILE": self.fichier_journal}.items():
            remplacement = mock.patch.object(server, nom, valeur)
            remplacement.start()
            self.addCleanup(remplacement.stop)

    def instantane(self) -> dict[str, bytes | None]:
        """Tous les fichiers et dossiers du sandbox, hors journal d'audit."""
        return {
            chemin.relative_to(self.racine).as_posix(): chemin.read_bytes() if chemin.is_file() else None
            for chemin in self.racine.rglob("*")
            if chemin != self.fichier_journal
        }

    def journal(self) -> list[dict]:
        if not self.fichier_journal.exists():
            return []
        return [json.loads(ligne) for ligne in self.fichier_journal.read_text(encoding="utf-8").splitlines()]

    def assertRefuse(self, outil: str, **arguments) -> str:
        est_erreur, texte = appel(outil, **arguments)
        self.assertTrue(est_erreur or texte.startswith(f"Erreur {outil}"), texte)
        self.assertNotIn("SECRET_HORS_SANDBOX", texte)
        return texte


class TestsContrat(TestAvecSandbox):
    def test_decouvre_cinq_outils_et_trois_ressources(self):
        outils = {outil.name for outil in requete_mcp("list_tools").tools}
        self.assertEqual(outils, {"read_file", "list_files", "write_file", "create_directory", "move_file"})
        ressources = {str(ressource.uri) for ressource in requete_mcp("list_resources").resources}
        self.assertEqual(
            ressources, {"filesystem://policy", "filesystem://manifest", "filesystem://audit/recent"}
        )

    def test_read_file_lit_un_fichier(self):
        self.assertEqual(appel("read_file", path="data/a.txt"), (False, "alpha\n"))

    def test_list_files_recursif_ou_non(self):
        _, texte = appel("list_files")
        self.assertEqual([entree["path"] for entree in json.loads(texte)["entries"]], ["data", "data/a.txt"])
        _, texte = appel("list_files", path=".", recursive=False)
        self.assertEqual([entree["path"] for entree in json.loads(texte)["entries"]], ["data"])

    def test_list_files_s_arrete_a_la_limite(self):
        with mock.patch.object(server, "MAX_LIST_ENTRIES", 1):
            listage = json.loads(appel("list_files")[1])
        self.assertTrue(listage["truncated"])
        self.assertEqual(len(listage["entries"]), 1)

    def test_write_file_ecrase_puis_ajoute(self):
        appel("write_file", path="note.txt", content="un", mode="overwrite", confirmed=True)
        appel("write_file", path="note.txt", content="deux", mode="append", confirmed=True)
        self.assertEqual((self.racine / "note.txt").read_text(encoding="utf-8"), "un\ndeux")
        appel("write_file", path="note.txt", content="trois", mode="overwrite", confirmed=True)
        self.assertEqual((self.racine / "note.txt").read_text(encoding="utf-8"), "trois")

    def test_create_directory_cree_les_parents(self):
        est_erreur, texte = appel("create_directory", path="a/b", confirmed=True)
        self.assertFalse(est_erreur, texte)
        self.assertTrue((self.racine / "a" / "b").is_dir())

    def test_move_file_est_reversible(self):
        avant = self.instantane()
        appel("move_file", source="data/a.txt", destination="b.txt", confirmed=True)
        self.assertEqual((self.racine / "b.txt").read_text(encoding="utf-8"), "alpha\n")
        self.assertFalse((self.racine / "data" / "a.txt").exists())
        appel("move_file", source="b.txt", destination="data/a.txt", confirmed=True)
        self.assertEqual(self.instantane(), avant)

    def test_ressources(self):
        appel("read_file", path="data/a.txt")
        self.assertIn("confirmed=true", lire_ressource("filesystem://policy"))
        manifeste = json.loads(lire_ressource("filesystem://manifest"))
        self.assertEqual(manifeste["entries"], [{"name": "data", "type": "directory"}])
        evenements = json.loads(lire_ressource("filesystem://audit/recent"))
        self.assertEqual(evenements[-1]["tool"], "read_file")


class TestsConsentement(TestAvecSandbox):
    def test_modifications_sans_consentement_ne_changent_rien(self):
        avant = self.instantane()
        for outil, arguments in MODIFICATIONS.items():
            for consentement in ({}, {"confirmed": False}):
                with self.subTest(outil=outil, consentement=consentement):
                    self.assertIn("Consentement requis", self.assertRefuse(outil, **arguments, **consentement))
                    self.assertEqual(self.journal()[-1]["outcome"], "denied_or_error")
        self.assertEqual(self.instantane(), avant)

    def test_confirmed_doit_etre_le_booleen_true(self):
        avant = self.instantane()
        for outil, arguments in MODIFICATIONS.items():
            for valeur in ("true", "yes", "1", 1):
                with self.subTest(outil=outil, confirmed=valeur):
                    self.assertRefuse(outil, **arguments, confirmed=valeur)
        self.assertEqual(self.instantane(), avant)


class TestsEntreesHostiles(TestAvecSandbox):
    def test_chemins_hors_sandbox_ou_reserves_refuses(self):
        chemins_hostiles = [
            "../../etc/passwd",
            "/etc/passwd",
            str(self.dehors),
            "../dehors.txt",
            "data/../../dehors.txt",
            "..\\dehors.txt",
            "data/a.txt\x00",
            "",
            "   ",
            ".",
            ".mcp_audit.jsonl",
            "./.mcp_audit.jsonl",
        ]
        appel("read_file", path="data/a.txt")  # garantit que le journal d'audit existe
        for chemin in chemins_hostiles:
            with self.subTest(chemin=chemin):
                self.assertRefuse("read_file", path=chemin)
                self.assertRefuse("write_file", path=chemin, content="pirate", mode="overwrite", confirmed=True)
        self.assertEqual(self.dehors.read_text(encoding="utf-8"), "SECRET_HORS_SANDBOX")
        self.assertRefuse("list_files", path="..")

    def test_liens_symboliques_refuses_et_caches(self):
        (self.racine / "lien.txt").symlink_to(self.dehors)
        (self.racine / "lien_dossier").symlink_to(self.base, target_is_directory=True)
        for chemin in ("lien.txt", "lien_dossier/dehors.txt"):
            with self.subTest(chemin=chemin):
                self.assertIn("liens symboliques", self.assertRefuse("read_file", path=chemin))
                self.assertRefuse("write_file", path=chemin, content="pirate", mode="append", confirmed=True)
        self.assertRefuse("move_file", source="lien.txt", destination="deplace.txt", confirmed=True)
        self.assertEqual(self.dehors.read_text(encoding="utf-8"), "SECRET_HORS_SANDBOX")
        self.assertNotIn("lien", appel("list_files")[1])
        self.assertNotIn("lien", lire_ressource("filesystem://manifest"))

    def test_mauvais_types_refuses(self):
        avant = self.instantane()
        cas = [
            ("read_file", {"path": 123}),
            ("list_files", {"recursive": "beaucoup"}),
            ("write_file", {"path": "t.txt", "content": 123, "mode": "overwrite", "confirmed": True}),
            ("write_file", {"path": "t.txt", "content": "x", "mode": "delete", "confirmed": True}),
            ("write_file", {"path": "t.txt", "content": "x", "confirmed": True}),  # mode absent
            ("create_directory", {"path": ["a", "b"], "confirmed": True}),
        ]
        for outil, arguments in cas:
            with self.subTest(outil=outil, arguments=arguments):
                self.assertRefuse(outil, **arguments)
        self.assertEqual(self.instantane(), avant)

    def test_limites_de_taille(self):
        gros = "x" * (server.MAX_FILE_BYTES + 1)
        self.assertIn("limite", self.assertRefuse("write_file", path="gros.txt", content=gros, mode="overwrite", confirmed=True))
        self.assertFalse((self.racine / "gros.txt").exists())
        (self.racine / "gros.txt").write_text(gros, encoding="utf-8")
        self.assertIn("limite", self.assertRefuse("read_file", path="gros.txt"))
        with mock.patch.object(server, "MAX_FILE_BYTES", 8):
            self.assertRefuse("write_file", path="data/a.txt", content="beta", mode="append", confirmed=True)
        self.assertEqual((self.racine / "data" / "a.txt").read_text(encoding="utf-8"), "alpha\n")

    def test_move_n_ecrase_jamais_et_n_imbrique_pas(self):
        (self.racine / "b.txt").write_text("bravo", encoding="utf-8")
        avant = self.instantane()
        self.assertIn("existe déjà", self.assertRefuse("move_file", source="data/a.txt", destination="b.txt", confirmed=True))
        self.assertRefuse("move_file", source="data", destination="data/interieur", confirmed=True)
        self.assertRefuse("move_file", source="absent.txt", destination="c.txt", confirmed=True)
        self.assertRefuse("write_file", path="manquant/c.txt", content="x", mode="overwrite", confirmed=True)
        self.assertRefuse("write_file", path="data", content="x", mode="overwrite", confirmed=True)
        self.assertEqual(self.instantane(), avant)


class TestsJournalAudit(TestAvecSandbox):
    def test_journal_enregistre_les_resultats_sans_le_contenu(self):
        appel("write_file", path="note.txt", content="MOT_DE_PASSE_42", mode="overwrite", confirmed=True)
        appel("read_file", path="../../etc/passwd")
        evenements = self.journal()
        self.assertEqual(
            [(evenement["tool"], evenement["outcome"]) for evenement in evenements],
            [("write_file", "approved_attempt"), ("write_file", "success"), ("read_file", "denied_or_error")],
        )
        self.assertEqual(evenements[0]["arguments"]["content_bytes"], len("MOT_DE_PASSE_42"))
        self.assertNotIn("MOT_DE_PASSE_42", self.fichier_journal.read_text(encoding="utf-8"))
        self.assertTrue(all("timestamp" in evenement for evenement in evenements))


if __name__ == "__main__":
    unittest.main()
