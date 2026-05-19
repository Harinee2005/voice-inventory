import { useEffect, useState } from 'react'
import { Activity, Plus, Minus, BarChart2 } from 'lucide-react'
import { getActivityFeed } from '../services/api'
import { formatDistanceToNow } from 'date-fns'

const utc = (ts) => ts ? new Date(ts.endsWith('Z') || ts.includes('+') ? ts : ts + 'Z') : null

const ACTION_ICONS = {
  Added:   { icon: Plus,     color: 'text-emerald-600 bg-emerald-50' },
  Removed: { icon: Minus,    color: 'text-red-500 bg-red-50' },
  Set:     { icon: BarChart2, color: 'text-indigo-600 bg-indigo-50' },
  Updated: { icon: Activity,  color: 'text-amber-600 bg-amber-50' },
}

export default function ActivityFeed({ refreshTrigger }) {
  const [logs, setLogs] = useState([])

  useEffect(() => {
    getActivityFeed(20).then(({ data }) => setLogs(data))
  }, [refreshTrigger])

  return (
    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
      <div className="px-5 py-4 border-b border-gray-100 flex items-center gap-2">
        <Activity className="w-4 h-4 text-indigo-500" />
        <span className="text-gray-800 font-semibold text-sm">Activity Feed</span>
      </div>
      <div className="overflow-y-auto max-h-56 p-3 space-y-1">
        {logs.length === 0 ? (
          <p className="text-center text-gray-400 text-sm py-6">No activity yet</p>
        ) : (
          logs.map((log) => {
            const config = ACTION_ICONS[log.action] || ACTION_ICONS.Updated
            const Icon = config.icon
            return (
              <div key={log.id} className="flex items-start gap-3 px-3 py-2 rounded-xl hover:bg-gray-50 transition-colors">
                <div className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 ${config.color}`}>
                  <Icon className="w-3.5 h-3.5" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-gray-700 text-xs leading-relaxed truncate">{log.details}</p>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-gray-400 text-xs">{log.worker}</span>
                    <span className="text-gray-200">·</span>
                    <span className="text-gray-400 text-xs">
                      {formatDistanceToNow(utc(log.timestamp), { addSuffix: true })}
                    </span>
                  </div>
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
