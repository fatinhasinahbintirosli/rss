from video_checker import rules, policy
import postolab_relay as relay


def cats(text):
    return {(h.rule.category, h.rule.severity) for h in rules.scan(text, "speech")}


def test_severe_profanity_blocks_mild_allowed():
    assert ("severe_profanity", "block") in cats("fuck you la")
    assert ("severe_profanity", "block") in cats("pukimak betul")
    assert ("severe_profanity", "block") in cats("dasar asshole")
    mild = cats("bodoh babi bangang sial")
    assert mild and all(c == "profanity" and s == "info" for c, s in mild)


def test_new_visual_categories_block_and_forced():
    for c in ("child_abuse", "weapons", "blood", "animal_abuse"):
        assert policy.VISUAL[c][0] == "block"
    fb = set(relay.DEFAULT_FORCE_BLOCK.split(","))
    assert {"visual_child_abuse", "visual_weapons", "text_severe_profanity", "visual_blood", "visual_animal_abuse"} <= fb
    assert {"visual_child_abuse", "visual_weapons", "text_severe_profanity", "visual_blood", "visual_animal_abuse"} <= relay.NEVER_POST


def test_allow_warn_cannot_unblock_weapons():
    c = relay.profile_cfg(relay.Cfg(), "deli", {}, {"allow_warn": ["visual_weapons", "low_resolution"]}, {})
    assert "visual_weapons" not in c.allow_warn
