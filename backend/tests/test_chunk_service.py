from app import chunk_service
from app.chunk_service import content_element_ids, elements_from_artifacts, infer_chunk_strategy
from app.chunking import ChunkStrategy, chunk_elements


def test_mineru_content_list_becomes_page_aware_elements():
    structured = {
        "content_list": [
            {"type": "text", "text": "项目背景", "page_idx": 0},
            {"type": "text", "text": "负责推荐系统升级", "page_idx": 1},
        ]
    }

    elements = elements_from_artifacts("", structured)

    assert [element.id for element in elements] == ["p1-e1", "p2-e2"]
    assert [element.page for element in elements] == [1, 2]
    assert elements[0].text == "项目背景"


def test_markdown_fallback_preserves_headings_as_elements():
    markdown = "# 工作经历\n\n## 星云科技\n\n负责推荐系统升级"
    elements = elements_from_artifacts(markdown, {})

    assert [element.kind for element in elements] == ["heading", "heading", "text"]
    assert elements[-1].text == "负责推荐系统升级"
    assert all(element.markdown_start is not None and element.markdown_end is not None for element in elements)
    assert all(markdown[element.markdown_start:element.markdown_end] == element.text for element in elements)
    assert all(element.source_locator == {} for element in elements)


def test_markdown_heading_with_immediate_body_keeps_body_for_chunking():
    markdown = "# 面试记录\n\n## 面试结论\n- 综合评分：4/5\n- 建议：进入下一轮\n\n## 风险\n跨行业经验需要继续核实"

    elements = elements_from_artifacts(markdown, {})

    assert [element.kind for element in elements] == ["heading", "heading", "text", "heading", "text"]
    assert elements[2].text == "- 综合评分：4/5\n- 建议：进入下一轮"
    assert elements[4].text == "跨行业经验需要继续核实"
    assert all(markdown[element.markdown_start:element.markdown_end] == element.text for element in elements)

    chunks = chunk_elements(elements, strategy=ChunkStrategy.MARKDOWN, chunk_size=100, chunk_overlap=0)
    assert [chunk.heading_path for chunk in chunks] == [["面试记录", "面试结论"], ["面试记录", "风险"]]
    assert [chunk.content for chunk in chunks] == ["- 综合评分：4/5\n- 建议：进入下一轮", "跨行业经验需要继续核实"]


def test_docx_locator_is_not_treated_as_auxiliary_source_position():
    markdown = "# 项目经历\n\n负责推荐系统升级"
    structured = {
        "content_list": [
            {"type": "title", "text": "项目经历", "locator": {"kind": "word_paragraph", "paragraph": 1}},
            {"type": "text", "text": "负责推荐系统升级", "locator": {"kind": "word_paragraph", "paragraph": 2}},
        ]
    }

    elements = elements_from_artifacts(markdown, structured)

    assert all(element.source_locator == {} for element in elements)
    assert [(element.markdown_start, element.markdown_end) for element in elements] == [(0, 6), (8, 16)]


def test_infer_chunk_strategy_uses_markdown_for_structured_documents():
    elements = elements_from_artifacts("# 项目经历\n\n负责推荐系统升级", {})

    assert infer_chunk_strategy(elements) == ChunkStrategy.MARKDOWN


def test_infer_chunk_strategy_falls_back_to_recursive_for_plain_text():
    elements = elements_from_artifacts(
        "第一段连续转录文本\n\n第二段连续转录文本",
        {
            "segments": [
                {"start": 0.0, "end": 12.0, "text": "第一段连续转录文本"},
                {"start": 12.0, "end": 24.0, "text": "第二段连续转录文本"},
            ]
        },
    )

    assert infer_chunk_strategy(elements) == ChunkStrategy.RECURSIVE


def test_mineru_content_list_adds_source_metadata_without_replacing_markdown_hierarchy():
    markdown = "# 项目经历\n\n## 推荐系统升级\n\n负责召回服务重构"
    structured = {
        "content_list": [
            {"type": "title", "text": "项目经历", "page_idx": 0},
            {"type": "title", "text": "推荐系统升级", "page_idx": 1},
            {"type": "text", "text": "负责召回服务重构", "page_idx": 1, "bbox": [10, 20, 300, 80]},
        ]
    }

    elements = elements_from_artifacts(markdown, structured)

    assert [element.text for element in elements] == ["# 项目经历", "## 推荐系统升级", "负责召回服务重构"]
    assert [element.page for element in elements] == [1, 2, 2]
    chunks = chunk_elements(elements, strategy=ChunkStrategy.MARKDOWN, chunk_size=100, chunk_overlap=0)
    assert chunks[0].heading_path == ["项目经历", "推荐系统升级"]
    assert chunks[0].source_locators[-1] == {
        "kind": "page_region",
        "page": 2,
        "bbox": [10, 20, 300, 80],
    }
    assert markdown[chunks[0].markdown_start:chunks[0].markdown_end] == chunks[0].content


def test_plain_transcript_chunks_keep_time_locators_without_heading_parents():
    structured = {
        "segments": [
            {"start": 0.0, "end": 30.0, "text": "第一段"},
            {"start": 30.0, "end": 65.0, "text": "第二段"},
        ]
    }

    elements = elements_from_artifacts("第一段\n\n第二段", structured)
    chunks = chunk_elements(elements, strategy=ChunkStrategy.RECURSIVE, chunk_size=100, chunk_overlap=10)

    assert all(chunk.heading_path == [] for chunk in chunks)
    assert any(locator.get("timestamp_start") == 0.0 for chunk in chunks for locator in chunk.source_locators)


def test_heading_path_expands_to_parent_tree_paths():
    assert chunk_service.parent_paths_for_heading(["项目经历", "推荐系统升级", "项目结果"]) == [
        ("项目经历",),
        ("项目经历", "推荐系统升级"),
        ("项目经历", "推荐系统升级", "项目结果"),
    ]


def test_coverage_denominator_excludes_headings_stored_as_metadata():
    elements = elements_from_artifacts("# 项目经历\n\n负责推荐系统升级", {})

    assert content_element_ids(elements) == ["md-e2"]
