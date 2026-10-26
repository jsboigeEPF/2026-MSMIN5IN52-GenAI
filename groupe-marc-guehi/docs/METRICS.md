# Métriques

Toutes les durées : `time.monotonic_ns()` sur la même machine (pas de dérive d'horloge inter-machines). Référence temporelle `t_ms` = ms depuis le début de la session.

## Définitions
| Métrique | Définition |
|---|---|
| **E2E (latence perçue)** | `playback_first_sample` − `vad_speech_end` (fin de parole utilisateur → premier échantillon audible ; émis par le serveur sur annonce du client, voir `playback_first_sample` plus bas : `e2e_ms` part de la déclaration de fin de tour, donc ajouter `end_silence_ms`). Le « premier octet audio » (`tts_first_byte`) est mesuré aussi ; l'écart = file + périphérique audio. |
| STT | `stt_final` − `vad_speech_end` (latence de finalisation) ; `stt_first_partial` − `vad_speech_start` si streaming |
| LLM TTFT | `llm_first_token` − `llm_request` |
| TTS TTFB | `tts_first_byte` − `tts_request` (requête déclenchée dès la première phrase complète) |
| Délai d'endpointing | `vad_speech_end` − fin réelle de parole (vérité terrain connue en replay) |
| **Temps d'arrêt après interruption** | `playback_stopped` − `barge_in_detected`, et `playback_stopped` − début réel de parole utilisateur (vérité terrain en replay) |
| Coût | Somme des coûts estimés par appel (voir CONSTRAINTS) |

## Événements ajoutés au prototype (traces)
État de la session (refactor 2026-10-24) : `state_change` (`from`, `to`), `state_illegal` (transition hors table, appliquée mais signalée), `task_error` (tâche de fond en échec ; `kind`, `error`).
`filler_sent` (texte, durée), `barge_in_detected` (parole détectée ≥ seuil), `playback_stopped` (accusé du client : `played_s`, `ack_ms`). Dans l'interface : « premier son » (accroche comprise) et « réponse » (contenu réel, après l'accroche) sont deux latences distinctes ; seule la seconde est comparable à la mesure sans accroche.

Événements ajoutés (étape 9) : `stt_speculative_start` (silence_ms), `stt_fallback` (STT de secours utilisé), `llm_tool_call` / `llm_tool_markup` (le LLM cherche), `search_request` / `search_done` (ms, chars) / `search_error`, `search_notice`, `filler_sent` avec `kind: "search"` pour l'accroche de recherche, `fallback_text_mode`, `mic_muted` / `mic_unmuted` (bouton « Couper le micro »). Le message `metrics` ajoute `search_ms` et `searched`. Sur un tour avec recherche : « premier son » = première accroche, « réponse » = contenu réel ; la durée de recherche est dans `search_ms` et ne doit pas être mélangée aux latences des tours sans recherche. Étape 10 : `filler_sent` avec `kind` = `search_announce` (annonce parlée écrite par le LLM) ou `search_wait` (2e phrase) ; `tts_fallback` (TTS de secours utilisé), `tts_error` (morceau non synthétisé) ; `search_notice` porte `announced`. Le STT spéculatif raccourcit `stt_ms` (mesuré depuis la fin de tour, pas depuis le début de l'appel).

`playback_first_sample` (audit C1) : le client annonce, au moment où il planifie le premier échantillon d'une réponse (accroche comprise), le délai avant qu'il soit audible (`playback_started.in_ms`, latence de sortie du navigateur comprise) ; le serveur ajoute ce délai à l'instant de réception et trace `e2e_ms` (depuis le début du tour, c'est-à-dire l'événement `vad_speech_end` déclaré après `vad.end_silence_ms` de silence : la latence perçue par l'utilisateur est donc `e2e_ms` + `end_silence_ms`, comme `first_sound_ms` du message `metrics`), `send_to_audible_ms` (file + périphérique audio, depuis l'envoi du premier octet) et `client_lead_ms`. **Limites** : trajet réseau local négligé ; non émis par les clients simulés (`ReplayClient`, `ws_client.py`), qui mesurent l'arrivée des octets ; non vérifié avec un vrai haut-parleur (Chrome en dry-run seulement).

Événements ajoutés (audit, 2026-10-26) : `ws_bad_message` (message client malformé ignoré ; `reason`), `turn_ignored` (transcription vide, bruit ou hallucination ; `reason`, `chars`), `llm_length` (réponse coupée par `llm.max_tokens`), `llm_fallback` (aussi sur erreur réseau ou échéance du 1er jeton).

## Interruption « correctement gérée »
Tous les critères vrais : (1) détectée (`barge_in_detected`) dans un délai maximal après le début de parole injectée (hypothèse initiale 500 ms ; valeur appliquée : `barge.thresholds.detect_max_ms` de `configs/campaign.yaml`) ; (2) lecture arrêtée rapidement après détection (hypothèse 300 ms, non mesurée par la campagne : seul « aucun audio après `interrupt` » l'est) ; (3) aucun échantillon de l'ancienne réponse joué ensuite ; (4) historique LLM tronqué au texte joué ; (5) la phrase interruptive est transcrite avec WER ≤ seuil et la réponse suivante y répond. Seuils = **[H]** ; ceux de la campagne sont dans `configs/campaign.yaml`. Faux positifs : barge-in déclenché sans parole injectée (comptés à part, sur bruit seul).
Taux = interruptions correctes / interruptions injectées.

## p50 / p95
Par condition (SNR, scénario), n ≥ 30 tours : `numpy.percentile(x, [50, 95])` (méthode linéaire, documentée). Avec n < 20, p95 non fiable : afficher min/max et intervalle bootstrap (1000 rééchantillons). Exclure les 3 premiers tours de chaque session (chauffe) ; les échecs sont comptés, non supprimés.

## WER
`jiwer` sur transcription finale vs texte de référence, après normalisation (minuscules, ponctuation, nombres) identique pour toutes les conditions. Reporté par SNR.

## Schéma JSONL (une ligne = un événement)
```json
{"run_id":"2026-10-21_a1","turn":3,"event":"stt_final","t_ms":12840.2,"ts_utc":"2026-10-21T09:12:03.412Z",
 "stage":"stt","provider":"school-whisper","route":"cloud",
 "data":{"text":"...","chars":42},"cost_eur":0.0003,"cfg":"configs/base.yaml#<hash>"}
```
Champs obligatoires : `run_id, turn, event, t_ms, ts_utc, stage, provider, route`. `data` libre mais sans clé API ni audio brut. Fichiers : `data/traces/<run_id>.jsonl`.

## Protocole de campagne (implémenté : `scripts/run_campaign.py`, paramètres dans `configs/campaign.yaml`)
Stimuli : 10 énoncés français sans chiffres (courts, moyens, longs), synthétisés une fois (voix homme/femme alternées, débit normal) et mis en cache ; graine fixe (`seed`), donc reproductible. Bruits : **blanc** et **foule** (4 pistes de voix synthétiques qui parlent en même temps, textes sans rapport) ; SNR ∈ {20, 10, 5, 0} dB + propre ; SNR = parole / bruit mesuré sur les segments de parole.
| Phase | Ce qui est mesuré | Réseau |
|---|---|---|
| `stt` | WER et temps de Whisper par énoncé × condition (2 tirages de bruit) | Whisper école |
| `vad` | hors ligne : segments manqués / coupés, erreur de début, délai de fin de tour, faux départs par minute de bruit seul (VAD seul, et seuil « barge-in » relevé) | aucun |
| `latency` | serveur réel, micro simulé en temps réel : 1er son et début de réponse (mesurés à l'arrivée des octets, et estimés par le serveur), étapes STT/LLM/TTS ; 5 conditions (propre sans/avec accroche, blanc 10 dB, foule 20 dB, foule 10 dB = cas limite) ; 1 tour de chauffe exclu ; une condition est abandonnée après 3 échecs (lignes `skipped`, non comptées comme échecs) | tout |
| `barge` | l'utilisateur parle par-dessus l'agent à +1, +3, +5 s après son 1er audio ; 2 phrases (courte, longue) × propre (2 reps) / blanc 10 dB / foule 20 dB ; faux positifs : l'agent parle, le bruit seul est allumé à ce moment-là | tout |
Hypothèses et limites : voix et foule **synthétiques** ; **pas d'écho** (le haut-parleur n'est pas réinjecté dans le micro : casque supposé) ; le niveau de bruit de fond est fixé une fois par rapport au RMS moyen des énoncés ; p95 indicatif tant que n < 20.

**Interruption correctement gérée** (voir ci-dessus, seuils dans `campaign.yaml`) : détectée dans `barge.thresholds.detect_max_ms` après le début de parole (la définition ci-dessus visait 500 ms : la confirmation exige `session.barge_in_min_speech_ms` de parole forte + `vad.min_speech_ms` de VAD, donc un délai minimal incompressible), aucun audio après le message `interrupt`, texte tronqué = début mot à mot de ce qui a été envoyé, phrase interruptive transcrite avec WER ≤ `barge.thresholds.wer_max` (avec ou sans la phrase précédente reprise), et une réponse audio suit.
### Écho (haut-parleur sans casque) — phases `echosweep` et `echo`, hors de `all`
Paramètres : section `echo:` de `configs/campaign.yaml` (retard, niveaux, signaux d'agent, réglages balayés). Niveau de l'écho = dB relatif au niveau moyen de la parole de référence (0 dB = écho aussi fort que l'utilisateur).
| Phase | Ce qui est mesuré | Réseau |
|---|---|---|
| `echosweep` | hors ligne, déterministe : part d'essais avec **faux barge-in** (écho seul) par niveau d'écho × seuil de probabilité × durée de parole forte ; interruptions de l'utilisateur **par-dessus l'écho** classées `ok` / `false` (déclenchée avant sa parole) / `late` / `missed` ; phrases coupées et délai de fin de tour après l'interruption ; pauses internes des énoncés pour l'étude de la fin de tour adaptative (`adaptive:`) | aucun |
| `echo` | en direct : la sortie de l'agent reçue par le client simulé est réinjectée dans le micro simulé (`EchoPath`, `MicSim`) ; faux barge-in (agent seul) puis interruptions par-dessus l'écho, mêmes critères que `barge` | tout (payant) |
**Limites** : écho **synthétique et propre** (un seul trajet retardé, sans réverbération de pièce, sans non-linéarité du haut-parleur, sans bruit) ; **l'annulation d'écho du navigateur (`echoCancellation`) n'est pas simulée**, donc ces mesures décrivent un cas pessimiste ; le signal d'agent de `echosweep` est une voix de synthèse de la campagne, pas la sortie réelle du serveur ; en direct, le niveau suppose que la voix de l'agent a le même niveau moyen que les stimuli [H] ; petits effectifs (intervalles de Wilson dans le notebook). Seule une mesure sur matériel réel (haut-parleurs, micro et navigateur) lève ces limites.

Exécution : `python scripts/run_campaign.py all --dry-run` (plan, durée, coût) puis `all` (≈ 30 min, ≈ 1–2 $) ; analyse : `notebooks/analysis.ipynb` (`CAMPAIGN_RUN=data/campaign/<run>`).
