export function buildEvaluationCaseQuery(baseQuery: string, groupByRun: boolean, offset: number) {
  const params = new URLSearchParams(baseQuery);
  params.set('offset', String(offset));
  params.set('limit', '10');
  if (groupByRun) params.set('group_by', 'run');
  return params.toString();
}
