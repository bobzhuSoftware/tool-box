import { useState, useEffect, useCallback, useRef } from 'react'

/**
 * Polls /api/teams-transcript/jobs every 3 s to keep the task list fresh.
 * Stops polling when there are no running jobs (interval drops to 10 s).
 * Reacts to token changes (login / logout).
 */
export default function useTeamsQueue(token) {
  const [jobs, setJobs] = useState([])
  const tokenRef = useRef(token)
  useEffect(() => { tokenRef.current = token }, [token])

  const fetchJobs = useCallback(async () => {
    if (!tokenRef.current) return
    try {
      const res = await fetch('/api/teams-transcript/jobs', {
        headers: { Authorization: `Bearer ${tokenRef.current}` },
      })
      if (!res.ok) return
      const data = await res.json()
      setJobs(data.jobs || [])
    } catch { /* ignore transient errors */ }
  }, [])

  useEffect(() => {
    if (!token) {
      setJobs([])
      return
    }

    fetchJobs()
    const id = setInterval(fetchJobs, 3000)
    return () => clearInterval(id)
  }, [token, fetchJobs])

  const deleteJob = useCallback(async (jobId) => {
    if (!tokenRef.current) return
    try {
      await fetch(`/api/teams-transcript/jobs/${jobId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${tokenRef.current}` },
      })
      setJobs(prev => prev.filter(j => j.job_id !== jobId))
    } catch { /* ignore */ }
  }, [])

  const runningCount = jobs.filter(j => j.status === 'running').length

  return { jobs, runningCount, fetchJobs, deleteJob }
}
