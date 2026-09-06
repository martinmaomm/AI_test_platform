/** Page-local polling: no overlapping requests or late updates after leaving. */
export const createBeatStatusMonitor = ({ fetchStatus, onUpdate, setTimer = setTimeout, clearTimer = clearTimeout }) => {
  let projectId = null
  let active = false
  let inFlight = false
  let generation = 0
  let timer = null
  let controller = null
  let state = { status: 'checking', checking: false }

  const emit = (next) => {
    state = next
    onUpdate(next)
  }
  const clearScheduledCheck = () => {
    if (timer !== null) clearTimer(timer)
    timer = null
  }
  const stop = () => {
    active = false
    generation += 1
    inFlight = false
    clearScheduledCheck()
    controller?.abort()
    controller = null
  }
  const refresh = async () => {
    if (!active || inFlight) return
    clearScheduledCheck()
    const requestId = ++generation
    controller = new AbortController()
    inFlight = true
    emit({ ...state, checking: true })
    try {
      const response = await fetchStatus(projectId, controller.signal)
      if (!active || requestId !== generation) return
      const payload = response?.data ?? response
      const status = ['online', 'offline', 'unknown'].includes(payload?.status) ? payload.status : 'unknown'
      emit({ status, checking: true })
    } catch {
      if (!active || requestId !== generation) return
      emit({ status: 'unknown', checking: true })
    } finally {
      if (active && requestId === generation) {
        inFlight = false
        controller = null
        emit({ ...state, checking: false })
        timer = setTimer(refresh, 15000)
      }
    }
  }
  const start = (nextProjectId) => {
    if (active && projectId === nextProjectId) return
    stop()
    projectId = nextProjectId
    active = nextProjectId !== null && nextProjectId !== undefined && nextProjectId !== ''
    if (active) {
      emit({ status: 'checking', checking: false })
      void refresh()
    }
  }
  return { start, stop, refresh }
}
