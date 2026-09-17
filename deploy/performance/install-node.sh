#!/usr/bin/env bash

set -Eeuo pipefail
umask 077
IFS=$'\n\t'

readonly OWNER_LABEL="com.automation-platform.owner"
readonly NODE_LABEL="com.automation-platform.node-id"
readonly PLATFORM_LABEL="com.automation-platform.platform"
readonly ROLE_LABEL="com.automation-platform.role"
readonly CONFIG_LABEL="com.automation-platform.config-sha256"
readonly OWNER_VALUE="performance-node-installer-v1"
readonly BASE_DIR="/opt/automation-performance-nodes"
readonly LOCK_DIR="/run/lock/automation-performance-nodes"
readonly DOCKER_GUIDE="https://docs.docker.com/engine/install/"

log() {
    printf '[性能节点安装] %s\n' "$*"
}

warn() {
    printf '[性能节点安装] 警告：%s\n' "$*" >&2
}

die() {
    printf '[性能节点安装] 错误：%s\n' "$*" >&2
    exit 1
}

usage() {
    cat >&2 <<'EOF'
用法：install-node.sh --platform URL --node-id UUID --token-file PATH [--ca-file PATH] --image-ref REF --image-id sha256:ID --image-config-id sha256:ID --archive-url HTTPSURL --archive-sha256 HEX --archive-size BYTES
EOF
}

require_value() {
    local option="$1"
    local count="$2"
    (( count >= 2 )) || {
        usage
        die "参数 ${option} 缺少值"
    }
}

platform=""
node_id=""
token_file=""
ca_file=""
image_ref=""
image_id=""
image_config_id=""
archive_url=""
archive_sha256=""
archive_size=""
seen_platform=0
seen_node_id=0
seen_token_file=0
seen_ca_file=0
seen_image_ref=0
seen_image_id=0
seen_image_config_id=0
seen_archive_url=0
seen_archive_sha256=0
seen_archive_size=0

while (( $# > 0 )); do
    case "$1" in
        --platform)
            require_value "$1" "$#"
            (( seen_platform == 0 )) || die "参数 --platform 不能重复"
            platform="$2"
            seen_platform=1
            shift 2
            ;;
        --node-id)
            require_value "$1" "$#"
            (( seen_node_id == 0 )) || die "参数 --node-id 不能重复"
            node_id="$2"
            seen_node_id=1
            shift 2
            ;;
        --token-file)
            require_value "$1" "$#"
            (( seen_token_file == 0 )) || die "参数 --token-file 不能重复"
            token_file="$2"
            seen_token_file=1
            shift 2
            ;;
        --ca-file)
            require_value "$1" "$#"
            (( seen_ca_file == 0 )) || die "参数 --ca-file 不能重复"
            ca_file="$2"
            seen_ca_file=1
            shift 2
            ;;
        --image-ref)
            require_value "$1" "$#"
            (( seen_image_ref == 0 )) || die "参数 --image-ref 不能重复"
            image_ref="$2"
            seen_image_ref=1
            shift 2
            ;;
        --image-id)
            require_value "$1" "$#"
            (( seen_image_id == 0 )) || die "参数 --image-id 不能重复"
            image_id="$2"
            seen_image_id=1
            shift 2
            ;;
        --image-config-id)
            require_value "$1" "$#"
            (( seen_image_config_id == 0 )) || die "参数 --image-config-id 不能重复"
            image_config_id="$2"
            seen_image_config_id=1
            shift 2
            ;;
        --archive-url)
            require_value "$1" "$#"
            (( seen_archive_url == 0 )) || die "参数 --archive-url 不能重复"
            archive_url="$2"
            seen_archive_url=1
            shift 2
            ;;
        --archive-sha256)
            require_value "$1" "$#"
            (( seen_archive_sha256 == 0 )) || die "参数 --archive-sha256 不能重复"
            archive_sha256="$2"
            seen_archive_sha256=1
            shift 2
            ;;
        --archive-size)
            require_value "$1" "$#"
            (( seen_archive_size == 0 )) || die "参数 --archive-size 不能重复"
            archive_size="$2"
            seen_archive_size=1
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            usage
            die "未知参数：$1"
            ;;
    esac
done

(( seen_platform && seen_node_id && seen_token_file && seen_image_ref && seen_image_id && seen_image_config_id && seen_archive_url && seen_archive_sha256 && seen_archive_size )) || {
    usage
    die "缺少必需参数"
}

required_commands=(curl sha256sum base64 flock docker stat install uname id tr)
for command_name in "${required_commands[@]}"; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        if [[ "$command_name" == "docker" ]]; then
            die "未找到 Docker。请按官方文档安装 Docker Engine 后重试：${DOCKER_GUIDE}；本安装器不会静默安装或升级 Docker"
        fi
        die "缺少必需工具：${command_name}"
    fi
done

[[ "$(id -u)" == "0" ]] || die "必须以 root 身份运行（例如 sudo bash install-node.sh ...）"
[[ "$(uname -s)" == "Linux" ]] || die "仅支持 Linux 节点，当前系统为 $(uname -s)"

system_name="Linux"
if [[ -r /etc/os-release ]]; then
    while IFS='=' read -r key value; do
        if [[ "$key" == "PRETTY_NAME" ]]; then
            value="${value#\"}"
            value="${value%\"}"
            system_name="$value"
            break
        fi
    done < /etc/os-release
fi
log "检测到系统：${system_name}"

node_id="$(tr '[:upper:]' '[:lower:]' <<<"$node_id")"
[[ "$node_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || die "--node-id 必须是标准 UUID"

validate_https_url() {
    local label="$1"
    local value="$2"
    local rest authority port
    [[ "$value" == https://* ]] || die "${label} 必须是 HTTPS URL"
    [[ ! "$value" =~ [[:space:][:cntrl:]\\] ]] || die "${label} 包含不安全字符"
    [[ "$value" != *"?"* && "$value" != *"#"* ]] || die "${label} 不允许查询参数或片段"
    rest="${value#https://}"
    authority="${rest%%/*}"
    [[ -n "$authority" && "$authority" != *"@"* ]] || die "${label} 主机无效或包含用户信息"
    if [[ ! "$authority" =~ ^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(:([0-9]{1,5}))?$ ]]; then
        die "${label} 主机或端口格式无效"
    fi
    port="${BASH_REMATCH[3]:-}"
    if [[ -n "$port" ]] && (( 10#$port < 1 || 10#$port > 65535 )); then
        die "${label} 端口必须在 1 到 65535 之间"
    fi
}

validate_https_url "--platform" "$platform"
validate_https_url "--archive-url" "$archive_url"
while [[ "$platform" == */ ]]; do
    platform="${platform%/}"
done

platform_authority="${platform#https://}"
platform_authority="${platform_authority%%/*}"
archive_authority="${archive_url#https://}"
archive_authority="${archive_authority%%/*}"
[[ "$(tr '[:upper:]' '[:lower:]' <<<"$platform_authority")" == "$(tr '[:upper:]' '[:lower:]' <<<"$archive_authority")" ]] || \
    die "--archive-url 必须与 --platform 使用同一 HTTPS 主机和端口"

[[ "$image_ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/:@-]*$ ]] || die "--image-ref 格式无效"
image_id="$(tr '[:upper:]' '[:lower:]' <<<"$image_id")"
image_config_id="$(tr '[:upper:]' '[:lower:]' <<<"$image_config_id")"
archive_sha256="$(tr '[:upper:]' '[:lower:]' <<<"$archive_sha256")"
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || die "--image-id 必须是 sha256: 加 64 位十六进制摘要"
[[ "$image_config_id" =~ ^sha256:[0-9a-f]{64}$ ]] || die "--image-config-id 必须是 sha256: 加 64 位十六进制摘要"
[[ "$archive_sha256" =~ ^[0-9a-f]{64}$ ]] || die "--archive-sha256 必须是 64 位十六进制摘要"
[[ "$archive_size" =~ ^[1-9][0-9]*$ ]] || die "--archive-size 必须是正整数字节数"

validate_secret_file() {
    local label="$1"
    local path="$2"
    local mode
    [[ "$path" == /* ]] || die "${label} 必须是绝对路径"
    [[ -f "$path" && ! -L "$path" ]] || die "${label} 必须是现有普通文件且不能是符号链接"
    mode="$(stat -c '%a' -- "$path")" || die "无法检查 ${label} 权限"
    [[ "$mode" == "600" ]] || die "${label} 权限必须是 0600，当前为 ${mode}"
}

validate_secret_file "--token-file" "$token_file"
token_file_identity="$(stat -c '%d:%i' -- "$token_file")" || die "无法记录 token 文件身份"
if (( seen_ca_file )); then
    validate_secret_file "--ca-file" "$ca_file"
    [[ "$ca_file" != "$token_file" ]] || die "--ca-file 与 --token-file 不能是同一文件"
fi

normalize_arch() {
    case "$1" in
        x86_64|amd64) printf 'amd64\n' ;;
        aarch64|arm64) printf 'arm64\n' ;;
        *) return 1 ;;
    esac
}

host_arch="$(normalize_arch "$(uname -m)")" || die "仅支持 Linux amd64/arm64，当前架构为 $(uname -m)"
log "宿主机架构：${host_arch}"

# With no name, inspect resolves the active context (including DOCKER_CONTEXT).
# Unlike `context show`, this also works on Docker 20.10 shipped by some NAS systems.
docker_context="$(docker context inspect --format '{{.Name}}' 2>/dev/null)" || die "无法读取 Docker context；请确认 Docker CLI 配置和权限"
docker_endpoint="$(docker context inspect "$docker_context" --format '{{ (index .Endpoints "docker").Host }}' 2>/dev/null)" || \
    die "无法检查 Docker context ${docker_context}"
[[ "$docker_endpoint" == unix://* ]] || die "拒绝远程 Docker context（${docker_context}: ${docker_endpoint}）；必须使用本机 unix socket"
if [[ -n "${DOCKER_HOST:-}" && "${DOCKER_HOST}" != unix://* ]]; then
    die "拒绝远程 DOCKER_HOST；必须使用本机 unix socket"
fi
if ! docker_info="$(docker info --format '{{.OSType}}/{{.Architecture}}' 2>/dev/null)"; then
    die "Docker 已安装但 daemon 不可用或当前 root 无访问权限。请启动本机 Docker 并按官方文档排查：${DOCKER_GUIDE}；本安装器不会安装或升级 Docker"
fi
daemon_os="${docker_info%%/*}"
daemon_arch_raw="${docker_info#*/}"
[[ "$daemon_os" == "linux" ]] || die "Docker daemon 必须运行 Linux 容器，当前为 ${daemon_os}"
daemon_arch="$(normalize_arch "$daemon_arch_raw")" || die "Docker daemon 架构不受支持：${daemon_arch_raw}"
[[ "$daemon_arch" == "$host_arch" ]] || die "Docker daemon 架构 ${daemon_arch} 与宿主机 ${host_arch} 不一致，疑似远程或错误 daemon"

ensure_secure_directory() {
    local path="$1"
    local description="$2"
    local mode owner
    if [[ -L "$path" ]]; then
        die "${description} 不能是符号链接：${path}"
    fi
    if [[ ! -e "$path" ]]; then
        mkdir -p -- "$path"
        chmod 0700 -- "$path"
    fi
    [[ -d "$path" && ! -L "$path" ]] || die "${description} 必须是普通目录：${path}"
    mode="$(stat -c '%a' -- "$path")"
    owner="$(stat -c '%u' -- "$path")"
    [[ "$mode" == "700" && "$owner" == "0" ]] || die "${description} 必须由 root 拥有且权限为 0700：${path}"
}

ensure_secure_directory "$LOCK_DIR" "安装锁目录"
lock_file="${LOCK_DIR}/${node_id}.lock"
[[ ! -L "$lock_file" ]] || die "节点锁文件不能是符号链接：${lock_file}"
exec 9>"$lock_file"
flock -n 9 || die "节点 ${node_id} 正在由另一个安装进程处理，请稍后重试"

ensure_secure_directory "$BASE_DIR" "节点部署根目录"
node_dir="${BASE_DIR}/${node_id}"
node_dir_created=0
if [[ ! -e "$node_dir" ]]; then
    mkdir -- "$node_dir"
    chmod 0700 -- "$node_dir"
    node_dir_created=1
fi
ensure_secure_directory "$node_dir" "节点专用目录"

ownership_file="${node_dir}/ownership"
expected_ownership="owner=${OWNER_VALUE}
node_id=${node_id}
platform=${platform}"
if (( node_dir_created )); then
    printf '%s\n' "$expected_ownership" >"$ownership_file"
    chmod 0600 -- "$ownership_file"
else
    [[ -f "$ownership_file" && ! -L "$ownership_file" ]] || die "已有节点目录缺少安全归属标记，不会接管：${node_dir}"
    [[ "$(stat -c '%a' -- "$ownership_file")" == "600" && "$(stat -c '%u' -- "$ownership_file")" == "0" ]] || \
        die "节点目录归属标记权限或属主不安全：${ownership_file}"
    [[ "$(<"$ownership_file")" == "$expected_ownership" ]] || die "节点目录归属与本次 node/platform 不一致，不会覆盖：${node_dir}"
fi

config_dir="${node_dir}/config"
ensure_secure_directory "$config_dir" "节点配置目录"
ca_digest="system"
if (( seen_ca_file )); then
    ca_digest="$(sha256sum -- "$ca_file")"
    ca_digest="${ca_digest%% *}"
fi
expected_config="image_ref=${image_ref}
image_id=${image_id}
image_config_id=${image_config_id}
archive_url=${archive_url}
archive_sha256=${archive_sha256}
archive_size=${archive_size}
ca_sha256=${ca_digest}"
config_sha256="$(printf '%s' "$expected_config" | sha256sum)"
config_sha256="${config_sha256%% *}"
config_file="${node_dir}/installation.conf"
if [[ -e "$config_file" ]]; then
    [[ -f "$config_file" && ! -L "$config_file" ]] || die "安装配置标记类型不安全：${config_file}"
    [[ "$(stat -c '%a' -- "$config_file")" == "600" && "$(stat -c '%u' -- "$config_file")" == "0" ]] || \
        die "安装配置标记权限或属主不安全：${config_file}"
    [[ "$(<"$config_file")" == "$expected_config" ]] || die "该节点已固定到另一镜像、归档、平台或 CA 配置；不会强行升级或覆盖"
else
    (( node_dir_created )) || die "已有节点目录缺少安装配置标记，不会推断或覆盖既有配置"
    printf '%s\n' "$expected_config" >"$config_file"
    chmod 0600 -- "$config_file"
fi

ca_copy="${config_dir}/platform-ca.crt"
if (( seen_ca_file )); then
    if [[ -e "$ca_copy" ]]; then
        [[ -f "$ca_copy" && ! -L "$ca_copy" ]] || die "持久 CA 文件类型不安全：${ca_copy}"
        existing_ca_digest="$(sha256sum -- "$ca_copy")"
        existing_ca_digest="${existing_ca_digest%% *}"
        [[ "$existing_ca_digest" == "$ca_digest" ]] || die "持久 CA 与本次 CA 不一致，不会覆盖"
    else
        install -o 10001 -g 10001 -m 0600 -- "$ca_file" "$ca_copy"
    fi
else
    [[ ! -e "$ca_copy" && ! -L "$ca_copy" ]] || die "该节点已有私有 CA 配置，本次不能省略 --ca-file"
fi

prefix="automation-performance-node-${node_id}"
runtime_container="${prefix}"
enroll_container="${prefix}-enroll"
check_container="${prefix}-identity-check"
identity_volume="${prefix}-identity"
node_network="${prefix}-network"

resource_exists() {
    local type="$1"
    local name="$2"
    docker "$type" inspect "$name" >/dev/null 2>&1
}

resource_label() {
    local type="$1"
    local name="$2"
    local label="$3"
    case "$type" in
        container)
            docker container inspect --format "{{ index .Config.Labels \"${label}\" }}" "$name" 2>/dev/null
            ;;
        network|volume)
            docker "$type" inspect --format "{{ index .Labels \"${label}\" }}" "$name" 2>/dev/null
            ;;
        *)
            die "内部错误：不支持检查 Docker ${type} 标签"
            ;;
    esac
}

verify_resource_ownership() {
    local type="$1"
    local name="$2"
    local role="$3"
    [[ "$(resource_label "$type" "$name" "$OWNER_LABEL")" == "$OWNER_VALUE" ]] || die "同名 Docker ${type} ${name} 不属于本安装器，不会接管"
    [[ "$(resource_label "$type" "$name" "$NODE_LABEL")" == "$node_id" ]] || die "同名 Docker ${type} ${name} 的节点归属不匹配，不会接管"
    [[ "$(resource_label "$type" "$name" "$PLATFORM_LABEL")" == "$platform" ]] || die "同名 Docker ${type} ${name} 的平台归属不匹配，不会接管"
    [[ "$(resource_label "$type" "$name" "$ROLE_LABEL")" == "$role" ]] || die "同名 Docker ${type} ${name} 的用途不匹配，不会接管"
    [[ "$(resource_label "$type" "$name" "$CONFIG_LABEL")" == "$config_sha256" ]] || die "同名 Docker ${type} ${name} 的固定配置不匹配，不会强行升级"
}

if resource_exists network "$node_network"; then
    verify_resource_ownership network "$node_network" "network"
else
    docker network create \
        --label "${OWNER_LABEL}=${OWNER_VALUE}" \
        --label "${NODE_LABEL}=${node_id}" \
        --label "${PLATFORM_LABEL}=${platform}" \
        --label "${ROLE_LABEL}=network" \
        --label "${CONFIG_LABEL}=${config_sha256}" \
        "$node_network" >/dev/null
    log "已创建节点专用 Docker 网络：${node_network}"
fi

if resource_exists volume "$identity_volume"; then
    verify_resource_ownership volume "$identity_volume" "identity"
else
    docker volume create \
        --label "${OWNER_LABEL}=${OWNER_VALUE}" \
        --label "${NODE_LABEL}=${node_id}" \
        --label "${PLATFORM_LABEL}=${platform}" \
        --label "${ROLE_LABEL}=identity" \
        --label "${CONFIG_LABEL}=${config_sha256}" \
        "$identity_volume" >/dev/null
    log "已创建节点专用身份卷：${identity_volume}"
fi

verify_local_image() {
    local actual_image_id image_platform image_os image_arch_raw image_arch
    actual_image_id="$(docker image inspect --format '{{.Id}}' "$image_ref" 2>/dev/null)" || return 1
    image_id_is_allowed "$actual_image_id" || \
        die "镜像引用 ${image_ref} 已指向 ${actual_image_id}，与固定 index ID ${image_id} 及 config ID ${image_config_id} 均不一致；不会覆盖或升级"
    image_platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image_ref")" || die "无法检查镜像平台"
    image_os="${image_platform%%/*}"
    image_arch_raw="${image_platform#*/}"
    [[ "$image_os" == "linux" ]] || die "固定镜像 OS 为 ${image_os}，不是 Linux"
    image_arch="$(normalize_arch "$image_arch_raw")" || die "固定镜像架构不受支持：${image_arch_raw}"
    [[ "$image_arch" == "$host_arch" && "$image_arch" == "$daemon_arch" ]] || \
        die "镜像架构 ${image_arch} 与宿主机/daemon 架构 ${host_arch}/${daemon_arch} 不匹配"
}

image_id_is_allowed() {
    local candidate
    candidate="$(tr '[:upper:]' '[:lower:]' <<<"$1")"
    [[ "$candidate" == "$image_id" || "$candidate" == "$image_config_id" ]]
}

image_ready=0
if docker image inspect "$image_ref" >/dev/null 2>&1; then
    verify_local_image
    image_ready=1
    log "本地镜像 ref、固定 ID 和 Linux/${host_arch} 平台核验通过，跳过归档下载与 docker load"
fi

if (( image_ready == 0 )); then
    archive_final="${node_dir}/image-${archive_sha256}.tar.gz"
    archive_partial="${archive_final}.part"
    verify_archive() {
        local path="$1"
        local actual_size actual_sha
        [[ -f "$path" && ! -L "$path" ]] || return 1
        actual_size="$(stat -c '%s' -- "$path")" || return 1
        [[ "$actual_size" == "$archive_size" ]] || return 1
        actual_sha="$(sha256sum -- "$path")" || return 1
        actual_sha="${actual_sha%% *}"
        [[ "$actual_sha" == "$archive_sha256" ]]
    }

    if [[ -e "$archive_final" || -L "$archive_final" ]]; then
        if verify_archive "$archive_final"; then
            log "复用已校验的镜像归档"
        else
            [[ -f "$archive_final" && ! -L "$archive_final" ]] || die "镜像归档路径类型不安全：${archive_final}"
            warn "已缓存镜像归档校验失败，将删除本节点目录内的损坏缓存后重新下载"
            rm -f -- "$archive_final"
        fi
    fi

    if [[ ! -e "$archive_final" ]]; then
        if [[ -e "$archive_partial" || -L "$archive_partial" ]]; then
            [[ -f "$archive_partial" && ! -L "$archive_partial" ]] || die "下载暂存路径类型不安全：${archive_partial}"
            partial_size="$(stat -c '%s' -- "$archive_partial")"
            if [[ "$partial_size" == "$archive_size" ]]; then
                if verify_archive "$archive_partial"; then
                    mv -- "$archive_partial" "$archive_final"
                else
                    warn "完整暂存文件摘要错误，已删除以便安全重下"
                    rm -f -- "$archive_partial"
                fi
            elif (( ${#partial_size} > ${#archive_size} )) || { (( ${#partial_size} == ${#archive_size} )) && [[ "$partial_size" > "$archive_size" ]]; }; then
                warn "暂存文件大于固定归档大小，已删除以便安全重下"
                rm -f -- "$archive_partial"
            fi
        fi
    fi

    if [[ ! -e "$archive_final" ]]; then
        log "正在通过受控 HTTPS 下载镜像归档（中断后可从暂存文件继续）"
        curl_args=(--fail --silent --show-error --proto '=https' --connect-timeout 15 --speed-limit 1024 --speed-time 120 --continue-at - --output "$archive_partial")
        if (( seen_ca_file )); then
            curl_args+=(--cacert "$ca_file")
        fi
        if ! curl "${curl_args[@]}" -- "$archive_url"; then
            die "镜像归档下载失败或连续 120 秒低于 1 KiB/s；已保留安全的 .part 暂存文件，可在网络恢复后用相同参数重试"
        fi
        actual_size="$(stat -c '%s' -- "$archive_partial")"
        if [[ "$actual_size" != "$archive_size" ]]; then
            if (( ${#actual_size} > ${#archive_size} )) || { (( ${#actual_size} == ${#archive_size} )) && [[ "$actual_size" > "$archive_size" ]]; }; then
                rm -f -- "$archive_partial"
                die "归档大小超过固定值（实际 ${actual_size}，期望 ${archive_size}），已删除异常暂存文件"
            fi
            die "归档下载不完整（实际 ${actual_size}，期望 ${archive_size}）；已保留暂存文件供续传"
        fi
        actual_sha="$(sha256sum -- "$archive_partial")"
        actual_sha="${actual_sha%% *}"
        if [[ "$actual_sha" != "$archive_sha256" ]]; then
            rm -f -- "$archive_partial"
            die "镜像归档 SHA256 校验失败（实际 ${actual_sha}）；已删除损坏暂存文件，未执行 docker load"
        fi
        mv -- "$archive_partial" "$archive_final"
    fi

    log "镜像归档大小与 SHA256 校验通过，正在载入 Docker"
    docker load --input "$archive_final" >/dev/null || die "docker load 失败；未启动或登记节点"
    verify_local_image || die "归档载入后未找到固定镜像引用 ${image_ref}"
fi

common_labels=(
    --label "${OWNER_LABEL}=${OWNER_VALUE}"
    --label "${NODE_LABEL}=${node_id}"
    --label "${PLATFORM_LABEL}=${platform}"
    --label "${CONFIG_LABEL}=${config_sha256}"
)
common_security=(
    --user 10001:10001
    --init
    --read-only
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m
    --cap-drop ALL
    --security-opt no-new-privileges
    --memory 512m
    --cpus 1
    --pids-limit 128
)
identity_mount=(--mount "type=volume,src=${identity_volume},dst=/var/lib/performance-node")
ca_runtime_args=()
if (( seen_ca_file )); then
    ca_runtime_args+=(
        -e PERFORMANCE_NODE_CA_BUNDLE=/run/secrets/platform-ca.crt
        --mount "type=bind,src=${ca_copy},dst=/run/secrets/platform-ca.crt,readonly"
    )
fi

if resource_exists container "$check_container"; then
    verify_resource_ownership container "$check_container" "identity-check"
    die "发现归属正确但未清理的身份检查容器 ${check_container}；请先诊断该容器状态，不会自动删除"
fi

identity_check_code=$'import json, os, stat, sys, uuid\np="/var/lib/performance-node/identity.json"\ntry:\n s=os.lstat(p)\nexcept FileNotFoundError:\n sys.exit(3)\nif stat.S_ISLNK(s.st_mode) or not stat.S_ISREG(s.st_mode) or stat.S_IMODE(s.st_mode)!=0o600:\n sys.exit(4)\ntry:\n d=json.load(open(p, encoding="utf-8")); n=str(uuid.UUID(d["node_id"])); t=d["agent_token"]; prefix, sep, secret=t.partition(".")\nexcept Exception:\n sys.exit(4)\nif set(d)!={"version","node_id","agent_token"} or d["version"]!=1 or n!=sys.argv[1] or not sep or not secret:\n sys.exit(4)\ntry:\n token_node=str(uuid.UUID(prefix))\nexcept Exception:\n sys.exit(4)\nsys.exit(0 if token_node==n else 4)'

check_identity() {
    local result
    set +e
    docker run --rm --name "$check_container" --network none \
        "${common_labels[@]}" --label "${ROLE_LABEL}=identity-check" \
        "${common_security[@]}" \
        --mount "type=volume,src=${identity_volume},dst=/var/lib/performance-node,readonly" \
        --entrypoint python "$image_ref" -c "$identity_check_code" "$node_id" >/dev/null
    result=$?
    set -e
    return "$result"
}

identity_status=0
if check_identity; then
    identity_status=0
else
    identity_status=$?
fi
if (( identity_status != 0 && identity_status != 3 )); then
    die "身份卷中的 identity.json 权限、格式或 node UUID 不安全；已停止且不会覆盖长期身份"
fi

cleanup_token_copy=""
cleanup_local_copy() {
    if [[ -n "$cleanup_token_copy" && -f "$cleanup_token_copy" && ! -L "$cleanup_token_copy" ]]; then
        rm -f -- "$cleanup_token_copy"
    fi
}
trap cleanup_local_copy EXIT

remove_source_token() {
    local current_identity
    if [[ ! -L "$token_file" && -f "$token_file" ]]; then
        current_identity="$(stat -c '%d:%i' -- "$token_file" 2>/dev/null || true)"
        if [[ "$current_identity" == "$token_file_identity" ]]; then
            rm -f -- "$token_file"
            log "已清理本地一次性 token 文件"
            return
        fi
    fi
    warn "token 文件在安装期间被替换或移除，未执行删除；请人工确认临时凭证已清理"
}

if (( identity_status == 3 )); then
    if resource_exists container "$runtime_container"; then
        verify_resource_ownership container "$runtime_container" "runtime"
        die "运行容器已存在但身份卷没有 identity.json；不会盲目登记或覆盖，请先诊断该容器"
    fi
    if resource_exists container "$enroll_container"; then
        verify_resource_ownership container "$enroll_container" "enroll"
        die "发现归属正确但未清理的登记容器 ${enroll_container}；不会自动删除或再次发送登记请求"
    fi
    cleanup_token_copy="${config_dir}/.enrollment-token.$$"
    [[ ! -e "$cleanup_token_copy" && ! -L "$cleanup_token_copy" ]] || die "临时 token 副本路径已存在，不会覆盖"
    install -o 10001 -g 10001 -m 0600 -- "$token_file" "$cleanup_token_copy"
    log "正在执行一次性节点登记；HTTP 仅尝试一次，响应不明确时不会自动重复消费凭证"
    enroll_once_code=$'import sys\nfrom performance_node.client import PerformanceNodeClient\nfrom performance_node.config import NodeConfig, enrollment_token_from_env\nfrom performance_node.errors import NodeError\nfrom performance_node.transport import AgentTransport\n\nclass SingleAttemptTransport(AgentTransport):\n    def post(self, url, payload, token=None, *, retry_transient=True):\n        return super().post(url, payload, token, retry_transient=False)\n\ntry:\n    config = NodeConfig.from_env()\n    client = PerformanceNodeClient(config, SingleAttemptTransport(config.ca_bundle))\n    client.enroll(enrollment_token_from_env())\nexcept NodeError as exc:\n    print(f"节点登记失败：{exc}", file=sys.stderr)\n    raise SystemExit(2)\nprint("节点注册成功，长期身份已安全保存")'
    enroll_result=0
    set +e
    docker run --rm --name "$enroll_container" --network "$node_network" \
        "${common_labels[@]}" --label "${ROLE_LABEL}=enroll" \
        "${common_security[@]}" \
        -e "PERFORMANCE_PLATFORM_URL=${platform}" \
        -e PERFORMANCE_NODE_STATE_DIR=/var/lib/performance-node \
        -e PERFORMANCE_NODE_ENROLLMENT_TOKEN_FILE=/run/secrets/enrollment-token \
        "${identity_mount[@]}" \
        --mount "type=bind,src=${cleanup_token_copy},dst=/run/secrets/enrollment-token,readonly" \
        "${ca_runtime_args[@]}" \
        --entrypoint python "$image_ref" -c "$enroll_once_code"
    enroll_result=$?
    set -e
    cleanup_local_copy
    cleanup_token_copy=""

    post_enroll_identity=0
    if check_identity; then
        post_enroll_identity=0
    else
        post_enroll_identity=$?
    fi
    if (( post_enroll_identity == 0 )); then
        remove_source_token
        if (( enroll_result != 0 )); then
            warn "登记容器返回异常，但该节点身份已安全落盘；将保留身份并继续恢复 runtime，绝不会再次消费 token"
        else
            log "节点登记成功，长期身份已写入专用卷"
        fi
    elif (( post_enroll_identity == 3 )); then
        die "节点登记未完成（可能是网络不可达、TLS、凭证过期、响应丢失或服务端拒绝）；未发现本地长期身份，未报告成功且不会自动重试。原 token 文件仅作受控排查保留，请先到平台确认状态并生成新命令，不要直接重复消费原 token"
    else
        die "登记后身份文件存在但校验失败；已保留身份卷和原 token，停止以避免再次消费凭证"
    fi
else
    log "检测到归属正确且有效的既有节点身份；跳过 enroll，不会重复消费 token"
    remove_source_token
fi

if resource_exists container "$runtime_container"; then
    verify_resource_ownership container "$runtime_container" "runtime"
    runtime_image_ref="$(docker container inspect --format '{{.Config.Image}}' "$runtime_container")"
    runtime_image_id="$(docker container inspect --format '{{.Image}}' "$runtime_container")"
    [[ "$runtime_image_ref" == "$image_ref" ]] || die "已有运行容器镜像引用与固定 image ref 不一致；不会强行升级或重建"
    image_id_is_allowed "$runtime_image_id" || \
        die "已有运行容器镜像 ID 与固定 index/config ID 均不一致；不会强行升级或重建"
    runtime_status="$(docker container inspect --format '{{.State.Status}}' "$runtime_container")"
    case "$runtime_status" in
        running)
            log "节点运行容器已经在运行；保留既有容器和身份，不重跑任务"
            ;;
        created|exited)
            log "节点运行容器当前为 ${runtime_status}，正在安全启动"
            docker start "$runtime_container" >/dev/null || die "已有节点容器启动失败；身份卷已保留"
            ;;
        *)
            die "节点运行容器状态为 ${runtime_status}，不会自动删除或重建；请先诊断"
            ;;
    esac
else
    log "正在创建受限的非 root 节点运行容器"
    docker run -d --name "$runtime_container" --network "$node_network" \
        "${common_labels[@]}" --label "${ROLE_LABEL}=runtime" \
        --restart unless-stopped --stop-timeout 25 \
        "${common_security[@]}" \
        --log-opt max-size=10m --log-opt max-file=3 \
        -e "PERFORMANCE_PLATFORM_URL=${platform}" \
        -e PERFORMANCE_NODE_STATE_DIR=/var/lib/performance-node \
        "${identity_mount[@]}" \
        "${ca_runtime_args[@]}" \
        "$image_ref" run >/dev/null || die "节点运行容器创建失败；长期身份卷已保留，下次用相同固定参数重试时不会再次登记"
fi

final_status="$(docker container inspect --format '{{.State.Status}}' "$runtime_container" 2>/dev/null)" || die "无法读取节点运行容器最终状态"
[[ "$final_status" == "running" ]] || die "节点运行容器最终状态为 ${final_status}，长期身份已保留"

log "安装完成：节点容器 ${runtime_container} 已启动"
log "注意：容器已启动不等于平台在线；请以平台页面收到的真实 heartbeat 状态为准"
