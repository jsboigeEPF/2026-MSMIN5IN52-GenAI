from src.orchestrator.history import History


def h(*roles):
    hist = History(max_messages=4)
    for i, role in enumerate(roles):
        hist.messages.append({"role": role, "content": f"{role}{i}"})
    return hist


def test_interrupt_of_unfinished_turn_appends_what_was_heard():
    hist = h("user")
    ref = hist.interrupt(False, "Première…", True)
    assert hist[-1] is ref and ref["content"] == "Première… [interrompu par l'utilisateur]"
    assert h("user").interrupt(False, "", True) is None


def test_interrupt_of_finished_turn_rewrites_or_drops_the_answer():
    hist = h("user", "assistant")
    ref = hist.interrupt(True, "Début", True)
    assert ref is hist[-1] and ref["content"].startswith("Début")
    hist = h("user", "assistant")
    assert hist.interrupt(True, "", True) is None and [m["role"] for m in hist.messages] == ["user"]


def test_refine_is_idempotent_and_targets_the_reference_only():
    hist = h("user", "assistant", "user", "assistant")
    first = hist.messages[1]
    assert hist.refine(first, "Mot", True) and hist.refine(first, "Mot", True)
    assert first["content"] == "Mot [interrompu par l'utilisateur]"
    assert hist[3]["content"] == "assistant3"


def test_refine_does_nothing_when_message_is_gone_or_nothing_heard():
    hist = h("user", "assistant")
    ref = hist.messages[1]
    assert not hist.refine(ref, "", True) and ref["content"] == "assistant1"
    hist.messages.clear()
    assert not hist.refine(ref, "Mot", True) and not hist.refine(None, "Mot", True)


def test_reference_survives_trimming_of_earlier_messages():
    hist = h("user", "assistant", "user", "assistant", "user")
    ref = hist.messages[3]
    hist.trim()                                  # max 4 : le plus ancien disparaît, les index se décalent
    assert hist.refine(ref, "Mot", False) and ref["content"] == "Mot"


def test_drop_pending_user_and_prefix():
    hist = h("assistant", "user")
    hist.drop_pending_user()
    hist.drop_pending_user()                     # le dernier est un message de l'agent : rien à retirer
    assert len(hist.messages) == 1
    hist.prefix_last(["Alors,", "Voyons."])
    assert hist[-1]["content"] == "Alors, Voyons. assistant0"


def test_interrupt_before_the_question_is_recorded_adds_no_second_agent_message():
    hist = h("user", "assistant")                # l'énoncé en cours n'est pas encore transcrit
    assert hist.interrupt(False, "Hmm, voyons.", True) is None
    assert [m["role"] for m in hist.messages] == ["user", "assistant"]
