"""설명문 파이프라인 실행 문서: 단계 결과 지우기."""

from app.models.expository import ExpositoryArticle, ExpositoryRun


def test_drop_from_removes_the_stage_results_and_the_finished_article():
    run = ExpositoryRun(
        run_id="expository-1",
        stage_outputs={"7_summary_glossary": {}, "8_translate": {}, "9_summarize": {}},
        stage_prompts={"7_summary_glossary": "a", "8_translate": "b", "9_summarize": "c"},
        article=ExpositoryArticle(
            title="t",
            summary="s",
            content_markdown="c",
            topic="topic",
            core_argument="thesis",
            english_title="e",
            pipeline_version="v1",
        ),
    )

    run.drop_from(["8_translate", "9_summarize"])

    assert list(run.stage_outputs) == ["7_summary_glossary"]
    assert list(run.stage_prompts) == ["7_summary_glossary"]
    assert run.article is None
