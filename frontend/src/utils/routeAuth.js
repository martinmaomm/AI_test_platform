export const currentUserFailureLocation = (to, authenticationFailed) =>
  authenticationFailed
    ? { path: '/login', query: { redirect: to.fullPath } }
    : { name: 'PermissionLoadFailure', query: { redirect: to.fullPath } }
