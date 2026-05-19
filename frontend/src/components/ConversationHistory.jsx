import { useEffect, useState } from 'react'
import { MessageSquare, User, Bot } from 'lucide-react'
import { getConversation } from '../services/api'
import { formatDistanceToNow } from 'date-fns'
const utc = (ts) => ts ? new Date(ts.endsWith('Z') || ts.includes('+') ? ts : ts + 'Z') : null

export default function ConversationHistory({ sessionId, refreshTrigger }) {
  const [messages, setMessages] = useState([])

  useEffect(() => {
    if (!sessionId) return
    getConversation(sessionId, 20).then(({ data }) => setMessages(data))
  }, [sessionId, refreshTrigger])

  return (
    <div className="overflow-y-auto max-h-80 space-y-2">
      {messages.length === 0 ? (
        <p className="text-center text-gray-400 text-sm py-6">No messages in this session</p>
      ) : (
        messages.map((msg) => (
          <div key={msg.id} className="flex items-start gap-2 px-2 py-1.5 rounded-lg hover:bg-gray-50 transition-colors">
            <div className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 mt-0.5 ${
              msg.role === 'user' ? 'bg-indigo-50 text-indigo-500' : 'bg-purple-50 text-purple-500'
            }`}>
              {msg.role === 'user' ? <User className="w-3 h-3" /> : <Bot className="w-3 h-3" />}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-gray-600 text-xs leading-relaxed line-clamp-2">{msg.content}</p>
              <span className="text-gray-300 text-xs">
                {formatDistanceToNow(utc(msg.timestamp), { addSuffix: true })}
              </span>
            </div>
          </div>
        ))
      )}
    </div>
  )
}
