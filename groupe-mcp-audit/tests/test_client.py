"""Tests du client de démonstration.

- Consentement : run_chat tourne contre le vrai serveur (session MCP en mémoire, sandbox
  temporaire) avec un faux Ollama qui renvoie des réponses écrites à l'avance.
- Pannes et Ctrl+C : le vrai client est lancé dans un processus séparé.
Lancement depuis la racine du projet : python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mcp.shared.memory import create_connected_server_and_client_session

import demo_client
import server
from test_server import TestAvecSandbox

PROJET = Path(__file__).resolve().parent.parent


def appel_outil(nom: str, arguments) -> dict:
    """Réponse du faux Ollama qui demande un appel d'outil."""
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": nom, "arguments": arguments}}]}


def texte(contenu: str) -> dict:
    """Réponse finale du faux Ollama."""
    return {"role": "assistant", "content": contenu}


ECRITURE = appel_outil("write_file", {"path": "note.txt", "content": "Bonjour", "mode": "overwrite"})


class TestsConsentementClient(TestAvecSandbox):
    def discuter(self, reponses_du_modele: list[dict], saisies: list) -> tuple[str, list[list[dict]]]:
        """Lance run_chat. Le faux Ollama renvoie reponses_du_modele dans l'ordre ;
        l'utilisateur tape saisies (une exception dans la liste est levée par input()).
        Renvoie l'affichage du client et les conversations reçues par le faux Ollama."""
        reponses = list(reponses_du_modele)
        requetes: list[list[dict]] = []

        def faux_ollama(url, modele, messages, outils):
            requetes.append([dict(message) for message in messages])
            return reponses.pop(0)

        async def executer():
            async with create_connected_server_and_client_session(server.mcp) as session:
                outils = (await session.list_tools()).tools
                await demo_client.run_chat(session, "faux-modele", "http://inutilise", outils)

        affichage = io.StringIO()
        with (
            mock.patch.object(demo_client, "ollama_chat", faux_ollama),
            mock.patch("builtins.input", side_effect=saisies),
            contextlib.redirect_stdout(affichage),
        ):
            asyncio.run(executer())
        return affichage.getvalue(), requetes

    def test_accord_oui_envoie_l_ecriture(self):
        affichage, _ = self.discuter([ECRITURE, texte("C'est écrit.")], ["Écris Bonjour", "OUI", "/quit"])
        self.assertEqual((self.racine / "note.txt").read_text(encoding="utf-8"), "Bonjour")
        self.assertEqual(self.journal()[-1]["outcome"], "success")
        self.assertIn("ACTION À CONFIRMER : write_file", affichage)
        self.assertIn("C'est écrit.", affichage)

    def test_refus_n_envoie_rien_et_arrete_la_demande(self):
        for refus in ("non", "oui", "OUI !", ""):
            with self.subTest(refus=refus):
                affichage, requetes = self.discuter([ECRITURE], ["Écris Bonjour", refus, "/quit"])
                self.assertFalse((self.racine / "note.txt").exists())
                self.assertEqual(self.journal(), [])  # le serveur n'a reçu aucun appel
                self.assertEqual(len(requetes), 1)  # le modèle n'est pas relancé
                self.assertIn("demande arrêtée", affichage)

    def test_fin_d_entree_pendant_la_confirmation_vaut_refus(self):
        affichage, _ = self.discuter([ECRITURE], ["Écris Bonjour", EOFError, EOFError])
        self.assertFalse((self.racine / "note.txt").exists())
        self.assertEqual(self.journal(), [])
        self.assertIn("Fin du client.", affichage)

    def test_ctrl_c_pendant_la_confirmation_n_envoie_rien(self):
        with self.assertRaises(BaseException) as contexte:
            self.discuter([ECRITURE], ["Écris Bonjour", KeyboardInterrupt])
        self.assertIsInstance(demo_client.root_error(contexte.exception), KeyboardInterrupt)
        self.assertFalse((self.racine / "note.txt").exists())
        self.assertEqual(self.journal(), [])

    def test_le_modele_ne_peut_pas_contourner_le_consentement(self):
        arguments = {"path": "note.txt", "content": "pirate", "mode": "overwrite", "confirmed": True}
        affichage, _ = self.discuter([appel_outil("write_file", arguments)], ["Écris", "non", "/quit"])
        self.assertIn("ACTION À CONFIRMER", affichage)
        self.assertFalse((self.racine / "note.txt").exists())
        self.assertEqual(self.journal(), [])

    def test_lecture_sans_confirmation_et_prompt_systeme(self):
        saisies = ["Lis a.txt", "/quit"]
        affichage, requetes = self.discuter(
            [appel_outil("read_file", {"path": "data/a.txt"}), texte("Il contient alpha.")], saisies
        )
        self.assertEqual(requetes[0][0], {"role": "system", "content": demo_client.SYSTEM_PROMPT})
        self.assertIn('Appel en lecture : read_file {"path": "data/a.txt"}', affichage)
        self.assertEqual(requetes[1][-1], {"role": "tool", "tool_name": "read_file", "content": "alpha\n"})
        self.assertNotIn("ACTION À CONFIRMER", affichage)

    def test_outil_inconnu_et_arguments_invalides_renvoyes_au_modele(self):
        _, requetes = self.discuter(
            [appel_outil("delete_all", {}), appel_outil("read_file", "{pas du json"), texte("Désolé.")],
            ["Supprime tout", "/quit"],
        )
        self.assertIn("Outil inconnu", requetes[1][-1]["content"])
        self.assertIn("Appel d'outil impossible", requetes[2][-1]["content"])
        self.assertEqual(self.journal(), [])

    def test_reponse_vide_signalee(self):
        affichage, _ = self.discuter([texte("")], ["Bonjour", "/quit"])
        self.assertIn("réponse vide", affichage)

    def test_limite_d_appels_par_demande(self):
        boucle = [appel_outil("list_files", {})] * demo_client.MAX_TOOL_ROUNDS
        affichage, requetes = self.discuter(boucle, ["Liste en boucle", "/quit"])
        self.assertEqual(len(requetes), demo_client.MAX_TOOL_ROUNDS)
        self.assertIn(f"limite de {demo_client.MAX_TOOL_ROUNDS} appels", affichage)


class TestsPannesEtCtrlC(unittest.TestCase):
    """Lance le vrai client (et donc le vrai server.py, qui ne fait que lister ses outils)."""

    def lancer(self, *options: str, dossier: Path = PROJET) -> subprocess.Popen:
        processus = subprocess.Popen(
            [sys.executable, "-u", "demo_client.py", *options],
            cwd=dossier,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for nettoyage in (processus.stdin.close, processus.stdout.close, processus.wait, processus.kill):
            self.addCleanup(nettoyage)  # exécutés dans l'ordre inverse : kill d'abord
        os.set_blocking(processus.stdout.fileno(), False)
        self.sortie = b""
        return processus

    def attendre(self, processus: subprocess.Popen, texte_attendu: str, delai: float = 20) -> None:
        limite = time.monotonic() + delai
        while texte_attendu.encode() not in self.sortie:
            self.assertLess(time.monotonic(), limite, f"{texte_attendu!r} jamais affiché :\n{self.sortie.decode()}")
            self.sortie += processus.stdout.read() or b""
            time.sleep(0.05)

    def terminer(self, processus: subprocess.Popen, delai: float = 5) -> str:
        """Attend la fin du processus et renvoie tout son affichage."""
        processus.wait(timeout=delai)
        self.sortie += processus.stdout.read() or b""
        return self.sortie.decode(errors="replace")

    def test_ollama_injoignable(self):
        processus = self.lancer("--ollama-url", "http://127.0.0.1:1")
        processus.stdin.write(b"Bonjour\n")
        processus.stdin.flush()
        affichage = self.terminer(processus, delai=20)
        self.assertEqual(processus.returncode, 1)
        self.assertIn("Impossible de joindre Ollama", affichage)
        self.assertNotIn("Traceback", affichage)

    def test_serveur_mcp_qui_ne_demarre_pas(self):
        dossier = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, dossier)
        shutil.copy(PROJET / "demo_client.py", dossier)  # sans server.py à côté
        processus = self.lancer(dossier=dossier)
        affichage = self.terminer(processus, delai=20)
        self.assertEqual(processus.returncode, 1)
        self.assertIn("le serveur MCP s'est arrêté ou n'a pas pu démarrer", affichage)
        self.assertNotIn("Traceback", affichage)

    def test_ctrl_c_a_l_invite(self):
        processus = self.lancer()
        self.attendre(processus, "Toi > ")
        processus.send_signal(signal.SIGINT)
        affichage = self.terminer(processus)
        self.assertEqual(processus.returncode, 0)
        self.assertIn("Fin du client.", affichage)
        self.assertNotIn("Traceback", affichage)

    def test_ctrl_c_pendant_l_attente_du_modele(self):
        # Faux Ollama qui accepte la connexion mais ne répond jamais.
        muet = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(muet.close)
        processus = self.lancer("--ollama-url", f"http://127.0.0.1:{muet.getsockname()[1]}")
        self.attendre(processus, "Toi > ")
        processus.stdin.write(b"Bonjour\n")
        processus.stdin.flush()
        time.sleep(1)  # le client attend maintenant la réponse du modèle
        processus.send_signal(signal.SIGINT)
        affichage = self.terminer(processus)
        self.assertEqual(processus.returncode, 0)
        self.assertIn("Fin du client.", affichage)
        self.assertNotIn("Traceback", affichage)


if __name__ == "__main__":
    unittest.main()
