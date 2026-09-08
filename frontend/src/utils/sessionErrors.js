// Business failures (SMTP/model authentication, invalid addresses, etc.) are not
// platform login failures. Some DRF JWT errors are wrapped in a 400 envelope.
export function isSessionExpiredError(error) {
  const status = error?.response?.status
  if (status === 401) return true
  if (status !== 400) return false
  const data = error.response.data
  return [data?.code, data?.error?.code, data?.error?.details?.code]
    .some(code => code === 'token_not_valid')
}
