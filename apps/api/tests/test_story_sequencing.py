"""Recommended implementation order from stories' free-text Dependencies
field — see app/services/story_sequencing.py."""

import uuid

from app.models.enums import StoryStatus
from app.services.story_sequencing import compute_story_order


class FakeStory:
    def __init__(self, title, dependencies="None.", status=StoryStatus.PENDING):
        self.id = uuid.uuid4()
        self.title = title
        self.dependencies = dependencies
        self.status = status


def test_a_story_with_no_dependencies_is_in_the_first_wave():
    a = FakeStory("Scaffold the database")
    result = compute_story_order([a])
    assert result.waves == [[a]]
    assert result.unresolved_dependencies == {} and result.circular == []


def test_independent_stories_land_in_the_same_wave():
    a = FakeStory("Scaffold the database")
    b = FakeStory("Set up CI pipeline")
    result = compute_story_order([a, b])
    assert len(result.waves) == 1 and {s.title for s in result.waves[0]} == {a.title, b.title}


def test_a_dependent_story_waits_for_its_whole_wave():
    a = FakeStory("Scaffold the database")
    b = FakeStory("Add user authentication", dependencies="Scaffold the database")
    result = compute_story_order([a, b])
    assert [{s.title for s in wave} for wave in result.waves] == [{"Scaffold the database"}, {"Add user authentication"}]


def test_a_chain_produces_one_story_per_wave():
    a = FakeStory("A")
    b = FakeStory("B", dependencies="A")
    c = FakeStory("C", dependencies="B")
    result = compute_story_order([a, b, c])
    assert [w[0].title for w in result.waves] == ["A", "B", "C"]


def test_multiple_dependencies_in_one_field_are_all_recognized():
    a = FakeStory("Admin User Authentication")
    b = FakeStory("Role-Based Access Control")
    c = FakeStory("Audit Trail Capture", dependencies="Admin User Authentication, Role-Based Access Control")
    result = compute_story_order([a, b, c])
    assert {s.title for s in result.waves[0]} == {"Admin User Authentication", "Role-Based Access Control"}
    assert result.waves[1] == [c]


def test_a_title_containing_commas_is_matched_whole_not_split():
    a = FakeStory("Scaffold New Subscription & Entitlement API, Admin Portal, and Isolated Database Projects")
    b = FakeStory("Deploy Subscription API", dependencies="Scaffold New Subscription & Entitlement API, Admin Portal, and Isolated Database Projects")
    result = compute_story_order([a, b])
    assert result.waves == [[a], [b]]
    assert result.unresolved_dependencies == {}


def test_longer_title_wins_over_a_short_title_that_is_its_substring():
    short = FakeStory("API")
    long = FakeStory("Decide API Gateway Routing Approach")
    dependent = FakeStory("Implement gateway", dependencies="Decide API Gateway Routing Approach")
    result = compute_story_order([short, long, dependent])
    # Only "long" should be recognized as the dependency, not "short" via substring collision.
    assert dependent not in result.waves[0]
    dependent_wave = next(w for w in result.waves if dependent in w)
    long_wave_index = next(i for i, w in enumerate(result.waves) if long in w)
    dependent_wave_index = result.waves.index(dependent_wave)
    assert dependent_wave_index > long_wave_index


def test_an_unresolvable_dependency_is_reported_not_dropped_silently():
    a = FakeStory("Real story", dependencies="A Story That Does Not Exist")
    result = compute_story_order([a])
    assert result.waves == [[a]]  # nothing actually blocks it
    assert result.unresolved_dependencies[str(a.id)] == "A Story That Does Not Exist"


def test_a_dependency_on_a_done_story_is_satisfied_and_excluded_from_waves():
    done = FakeStory("Already shipped", status=StoryStatus.DONE)
    a = FakeStory("Build on top", dependencies="Already shipped")
    result = compute_story_order([done, a])
    assert result.waves == [[a]]
    assert result.unresolved_dependencies == {}  # the DONE title must not show as unresolved


def test_a_two_story_cycle_is_reported_as_circular_not_silently_dropped():
    a = FakeStory("A", dependencies="B")
    b = FakeStory("B", dependencies="A")
    result = compute_story_order([a, b])
    assert result.waves == []
    assert {s.title for s in result.circular} == {"A", "B"}


def test_a_cycle_does_not_block_unrelated_stories():
    a = FakeStory("A", dependencies="B")
    b = FakeStory("B", dependencies="A")
    c = FakeStory("C")
    result = compute_story_order([a, b, c])
    assert result.waves == [[c]]
    assert {s.title for s in result.circular} == {"A", "B"}


def test_trivial_dependency_text_means_no_dependency():
    for text in ("None.", "none", "N/A", "-", "TBD"):
        a = FakeStory("Solo", dependencies=text)
        result = compute_story_order([a])
        assert result.waves == [[a]] and result.unresolved_dependencies == {}


def test_a_story_cannot_depend_on_itself():
    a = FakeStory("Self Referential Story", dependencies="Self Referential Story")
    result = compute_story_order([a])
    assert result.waves == [[a]] and result.circular == []
