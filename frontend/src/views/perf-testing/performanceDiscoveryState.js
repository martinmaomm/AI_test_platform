const text = (value) => (typeof value === "string" ? value.trim() : value);

/**
 * Keep the create request deliberately separate from the editor-only target_id.
 * The discovery create serializer is strict and accepts exactly these fields.
 */
export const buildPerformanceDiscoveryCreatePayload = (form) => ({
  target_url: text(form.target_url),
  description: text(form.description),
  model_id: form.model_id,
  api_origin: text(form.api_origin) || null,
  allow_test_data_writes: form.allow_test_data_writes === true,
  exploration_timeout_seconds: form.exploration_timeout_seconds,
});

/**
 * `public_summary` is the server-redacted view intended for the UI.  Keep the
 * request variants visible without ever reconstructing a raw captured request.
 */
export const publicDiscoverySamplePreview = (record) => {
  const summary = record?.public_summary || {};
  const request = summary.observed_request || {};
  const response = summary.observed_response || {};
  return {
    query: request.query ?? [],
    headers: request.headers ?? {},
    json: request.json,
    form: request.form,
    response: {
      headers: response.headers ?? {},
      body: response.body,
    },
  };
};
