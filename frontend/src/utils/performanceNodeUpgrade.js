const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_RE = /^[a-f0-9]{64}$/i;
const DOCKER_NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$/;
const VERSION_RE = /^[0-9][0-9A-Za-z.+_-]{0,63}$/;
const CONTROL_RE = /[\x00-\x1f\x7f]/;

export const isDockerContainerName = (value) =>
  typeof value === "string" && DOCKER_NAME_RE.test(value);

export const isComposeServiceName = isDockerContainerName;

const isHttpsUrl = (value) => {
  if (typeof value !== "string" || CONTROL_RE.test(value)) return false;
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      Boolean(url.hostname) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash
    );
  } catch {
    return false;
  }
};

const isImmutableImage = (value) =>
  typeof value === "string" &&
  !CONTROL_RE.test(value) &&
  /^[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[a-f0-9]{64}$/i.test(value);

const shellQuote = (value) => `'${String(value).replace(/'/g, "'\"'\"'")}'`;

export const validPerformanceNodeUpgrade = (node, upgrade) => {
  if (!upgrade?.available || !performanceNodeReadyForUpgrade(node))
    return false;
  return (
    UUID_RE.test(String(upgrade.node_id || "")) &&
    isHttpsUrl(upgrade.platform_url) &&
    isHttpsUrl(upgrade.script_url) &&
    SHA256_RE.test(String(upgrade.script_sha256 || "")) &&
    isImmutableImage(upgrade.image_ref) &&
    VERSION_RE.test(String(upgrade.agent_version || ""))
  );
};

export const performanceNodeHasActiveRuns = (node) =>
  Math.max(0, Number(node?.active_run_count) || 0) > 0;

export const performanceNodeReadyForUpgrade = (node) =>
  (typeof node?.active_run_count === "number" &&
    Number.isInteger(node.active_run_count) &&
    node.active_run_count === 0) ||
  (typeof node?.active_run_count === "string" &&
    /^\d+$/.test(node.active_run_count) &&
    Number(node.active_run_count) === 0);

export const buildPerformanceNodeUpgradeCommand = (
  node,
  upgrade,
  container = "",
) => {
  const trimmedContainer = String(container || "").trim();
  if (
    !validPerformanceNodeUpgrade(node, upgrade) ||
    (trimmedContainer && !isDockerContainerName(trimmedContainer))
  )
    return null;

  const scriptPath = '"$upgrade_dir/upgrade.py"';
  const args = [
    "--node-id",
    upgrade.node_id,
    "--platform",
    upgrade.platform_url,
    "--image",
    upgrade.image_ref,
    "--agent-version",
    upgrade.agent_version,
  ];
  if (trimmedContainer) args.push("--container", trimmedContainer);
  const quotedArgs = args.map(shellQuote).join(" ");
  return [
    "bash <<'PERFORMANCE_NODE_UPGRADE_EOF'",
    "set -euo pipefail",
    "umask 077",
    "for required in python3 curl sha256sum docker mktemp; do",
    '  command -v "$required" >/dev/null 2>&1 || { echo "缺少命令: $required" >&2; exit 1; }',
    "done",
    "python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' || { echo '需要 Python 3.8 或更高版本' >&2; exit 1; }",
    'upgrade_dir="$(mktemp -d)"',
    "trap 'rm -rf \"$upgrade_dir\"' EXIT",
    `curl --fail --silent --show-error --insecure --proto '=https' --connect-timeout 10 --max-time 60 --max-redirs 0 --output ${scriptPath} ${shellQuote(upgrade.script_url)}`,
    `printf '%s  %s\\n' ${shellQuote(upgrade.script_sha256)} ${scriptPath} | sha256sum -c -`,
    `python3 ${scriptPath} ${quotedArgs}`,
    "PERFORMANCE_NODE_UPGRADE_EOF",
  ].join("\n");
};

export const buildPerformanceNodeComposeCommand = (node, upgrade, service) => {
  const trimmedService = String(service || "").trim();
  if (
    !validPerformanceNodeUpgrade(node, upgrade) ||
    !isComposeServiceName(trimmedService)
  )
    return null;
  const quoted = shellQuote(trimmedService);
  return `docker compose pull -- ${quoted}\ndocker compose up -d --no-deps --no-build --pull never -- ${quoted}`;
};
