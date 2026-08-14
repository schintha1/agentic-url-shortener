"""A deliberately failing suite.

Used only as a target for the orchestrator's test stage, to prove that a red
domain suite actually fails the run. Excluded from normal collection by the
`fixtures` path being outside `testpaths`.
"""


def test_this_always_fails() -> None:
    observed = 1
    expected = 2
    assert observed == expected, "intentional failure used to verify the SDLC test gate"
