const rawDebugSteps = (result) => {
  const value =
    result?.step_datas ||
    result?.steps ||
    result?.results ||
    result?.debug_steps ||
    result?.result?.step_datas ||
    result?.result?.steps ||
    [];
  return Array.isArray(value) ? value : [];
};

export const normalizeDebugSteps = (result) =>
  rawDebugSteps(result).map((step) => {
    const data = step?.data || {};
    const reqResps = data.req_resps || step?.req_resps || [];
    const requests = reqResps.map((item) => item?.request).filter(Boolean);
    const responses = reqResps.map((item) => item?.response).filter(Boolean);
    return {
      ...step,
      request: requests.length === 1 ? requests[0] : requests,
      response: responses.length === 1 ? responses[0] : responses,
      exportVariables: step?.export_vars || step?.extract || step?.extractions,
      extractionResults:
        step?.extraction_results ?? data?.extraction_results ?? null,
      assertions:
        data.validators ||
        step?.validators ||
        step?.assertions ||
        step?.validate,
    };
  });
