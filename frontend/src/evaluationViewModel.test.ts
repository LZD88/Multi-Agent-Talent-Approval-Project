import { describe, expect, it } from 'vitest';

import { buildEvaluationCaseQuery } from './evaluationViewModel';

describe('evaluation view model', () => {
  it('uses the ordinary case list unless Run grouping is enabled', () => {
    expect(buildEvaluationCaseQuery('module_key=fact_extraction', false, 20)).toBe(
      'module_key=fact_extraction&offset=20&limit=10',
    );
    expect(buildEvaluationCaseQuery('module_key=fact_extraction', true, 20)).toBe(
      'module_key=fact_extraction&offset=20&limit=10&group_by=run',
    );
  });
});
