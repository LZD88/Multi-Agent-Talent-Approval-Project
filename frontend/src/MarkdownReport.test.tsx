import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { MarkdownReport } from "./TalentChatPage";

describe("MarkdownReport", () => {
  it("renders GFM structure and keeps evidence references interactive", () => {
    const html = renderToStaticMarkup(
      <MarkdownReport
        text={[
          "# 候选人报告",
          "",
          "**结论**",
          "",
          "- 第一项",
          "- 第二项 [E1](/api/evidence/citations/chunk-1)",
          "",
          "| 候选人 | 得分 |",
          "| --- | ---: |",
          "| C001 | 80 |",
        ].join("\n")}
        onCitation={vi.fn()}
      />,
    );

    expect(html).toContain("<h1>候选人报告</h1>");
    expect(html).toContain("<strong>结论</strong>");
    expect(html).toContain("<ul>");
    expect(html).toContain("<table>");
    expect(html).toContain('class="citationLink"');
  });
});
