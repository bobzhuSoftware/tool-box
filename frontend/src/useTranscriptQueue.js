import { useState, useEffect, useCallback, useRef } from 'react'

/**
 * Polls /api/transcribe/jobs every 3 s to keep the transcript task list fresh.
 * Reacts to token changes (login / logout).
 */
export default function useTranscriptQueue(token) {
  const [jobs, setJobs] = useState([])
  const tokenRef = useRef(token)
  useEffect(() => { tokenRef.current = token }, [token])

  const fetchJobs = useCallback(async () => {
    if (!tokenRef.current) return
    try {
      const res = await fetch('/api/transcribe/jobs', {
        headers: { Authorization: `Bearer ${tokenRef.current}` },
      })
      if (!res.ok) return
      const data = await res.json()
      setJobs(data.jobs || [])
    } catch { /* ignore transient errors */ }
  }, [])

  useEffect(() => {
    if (!token) { setJobs([]); return }
    fetchJobs()
    const id = setInterval(fetchJobs, 3000)
    return () => clearInterval(id)
  }, [token, fetchJobs])

  const deleteJob = useCallback(async (jobId) => {
    if (!tokenRef.current) return
    try {
      await fetch(`/api/transcribe/jobs/${jobId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${tokenRef.current}` },
      })
      setJobs(prev => prev.filter(j => j.job_id !== jobId))
    } catch { /* ignore */ }
  }, [])

  return {
    jobs,
    runningCount: jobs.filter(j => j.status === 'running').length,
    fetchJobs,
    deleteJob,
  }
}
