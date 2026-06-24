import plistlib

from scripts.install_launchd import (
    LESSON_GENERATOR_LABEL,
    RUNNER_LABEL,
    _build_lesson_generator_plist,
    _build_runner_plist,
)


class TestLessonGeneratorPlist:
    def test_has_correct_label(self):
        plist = _build_lesson_generator_plist()
        assert plist["Label"] == LESSON_GENERATOR_LABEL

    def test_start_interval_is_1800(self):
        plist = _build_lesson_generator_plist()
        assert plist["StartInterval"] == 1800

    def test_program_arguments_include_generate(self):
        plist = _build_lesson_generator_plist()
        args = plist["ProgramArguments"]
        assert str(args[-1]) == "--generate"

    def test_program_arguments_use_module(self):
        plist = _build_lesson_generator_plist()
        args = plist["ProgramArguments"]
        assert "-m" in args
        assert "daglas.run" in args

    def test_has_working_directory(self):
        plist = _build_lesson_generator_plist()
        assert "WorkingDirectory" in plist

    def test_no_run_at_load(self):
        plist = _build_lesson_generator_plist()
        assert "RunAtLoad" not in plist

    def test_no_keep_alive(self):
        plist = _build_lesson_generator_plist()
        assert "KeepAlive" not in plist

    def test_plist_serializes_to_valid_xml(self):
        plist = _build_lesson_generator_plist()
        xml = plistlib.dumps(plist)
        parsed = plistlib.loads(xml)
        assert parsed["Label"] == LESSON_GENERATOR_LABEL


class TestRunnerPlist:
    def test_has_correct_label(self):
        plist = _build_runner_plist()
        assert plist["Label"] == RUNNER_LABEL

    def test_run_at_load_is_true(self):
        plist = _build_runner_plist()
        assert plist["RunAtLoad"] is True

    def test_keep_alive_is_true(self):
        plist = _build_runner_plist()
        assert plist["KeepAlive"] is True

    def test_program_arguments_use_module(self):
        plist = _build_runner_plist()
        args = plist["ProgramArguments"]
        assert "-m" in args
        assert "daglas.run" in args

    def test_has_working_directory(self):
        plist = _build_runner_plist()
        assert "WorkingDirectory" in plist

    def test_no_start_interval(self):
        plist = _build_runner_plist()
        assert "StartInterval" not in plist

    def test_plist_serializes_to_valid_xml(self):
        plist = _build_runner_plist()
        xml = plistlib.dumps(plist)
        parsed = plistlib.loads(xml)
        assert parsed["Label"] == RUNNER_LABEL
