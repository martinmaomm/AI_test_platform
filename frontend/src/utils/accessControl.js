export const isSuperuser = (user) => user?.is_superuser === true;

export const isPlatformAdmin = (user) =>
  isSuperuser(user) || user?.role === "admin" || user?.is_staff === true;

export const canManageUsers = isPlatformAdmin;
export const canManageProjectMetadata = isPlatformAdmin;

export const canEditManagedUser = (operator, target) => {
  if (!target || String(operator?.id) === String(target.id)) return false;
  if (target.is_superuser) return false;
  return (
    isSuperuser(operator) ||
    (isPlatformAdmin(operator) && target.role !== "admin" && !target.is_staff)
  );
};

export const canAccessRoute = (user, route) =>
  !route.meta?.requiresPlatformAdmin || isPlatformAdmin(user);

export const visibleDashboardItems = (items, user) => {
  if (isPlatformAdmin(user)) return items;
  const restricted = new Set([
    "portal-ai-config",
    "portal-settings",
  ]);
  return items.filter((item) => !restricted.has(item.i));
};
