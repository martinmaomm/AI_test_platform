export const EMPTY_DASHBOARD_SUMMARY = Object.freeze({
  today_pass_rate: 0,
  today_executions: 0,
  ai_contribution_rate: 0,
  total_cases: 0,
});

export const dashboardProjectsFromResponse = (response) => {
  const data = response?.data ?? response;
  if (Array.isArray(data)) return data;
  return Array.isArray(data?.items) ? data.items : [];
};

// Every selection starts a new generation. Responses from older generations
// must not update the currently displayed project.
export const createDashboardRequestGate = () => {
  let generation = 0;

  return {
    begin(projectId) {
      generation += 1;
      return { generation, projectId };
    },
    isCurrent(request) {
      return request.generation === generation;
    },
  };
};
