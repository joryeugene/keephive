from keephive.skillpack import render_platform_skill


def test_render_platform_skill_includes_platform_note():
    render = render_platform_skill("gemini")
    assert "Gemini CLI" in render.title
    assert "Gemini CLI" in render.content
    assert render.hash
    assert "hooks" in render.content.lower()
