import { useState, useEffect } from 'react'
import { Brain, Smile, Meh, Frown, AlertTriangle, Trash2, RefreshCw, BookOpen, Zap, User } from 'lucide-react'
import { getUserProfile, getUserLexicons, deleteLexicon } from '../services/api'

const EMOTION_CONFIG = {
  happy:      { icon: Smile,        color: 'text-emerald-500', bg: 'bg-emerald-50 border-emerald-200', label: 'Happy',      desc: 'Enthusiastic and positive' },
  neutral:    { icon: Meh,          color: 'text-blue-500',    bg: 'bg-blue-50 border-blue-200',       label: 'Neutral',    desc: 'Normal work tone' },
  frustrated: { icon: Frown,        color: 'text-amber-500',   bg: 'bg-amber-50 border-amber-200',     label: 'Frustrated', desc: 'Showing signs of irritation' },
  angry:      { icon: AlertTriangle, color: 'text-red-500',    bg: 'bg-red-50 border-red-200',         label: 'Angry',      desc: 'Explicit frustration detected' },
}

const WORD_TYPE_COLORS = {
  unit:         'bg-purple-50 text-purple-600 border-purple-200',
  item:         'bg-emerald-50 text-emerald-600 border-emerald-200',
  abbreviation: 'bg-blue-50 text-blue-600 border-blue-200',
  slang:        'bg-amber-50 text-amber-600 border-amber-200',
  unknown:      'bg-gray-50 text-gray-500 border-gray-200',
}

const TONE_LABELS = {
  friendly_fun: { label: 'Friendly & Fun', color: 'text-emerald-600', bg: 'bg-emerald-50 border-emerald-200' },
  formal:       { label: 'Formal Mode',    color: 'text-slate-600',   bg: 'bg-slate-50 border-slate-200' },
}

function EmptyState({ message }) {
  return (
    <div className="flex flex-col items-center justify-center py-10 text-gray-400">
      <Brain className="w-10 h-10 mb-2 opacity-30" />
      <p className="text-sm">{message}</p>
    </div>
  )
}

export default function UserInsights({ workerId, refreshTrigger }) {
  const [profile, setProfile] = useState(null)
  const [lexicons, setLexicons] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [deleting, setDeleting] = useState(null)

  const load = async () => {
    if (!workerId) return
    setLoading(true)
    setError(null)
    try {
      const [profileRes, lexRes] = await Promise.all([
        getUserProfile(workerId),
        getUserLexicons(workerId),
      ])
      setProfile(profileRes.data)
      setLexicons(lexRes.data)
    } catch (e) {
      if (e.response?.status === 404) {
        setProfile(null)
        setLexicons([])
      } else {
        setError('Failed to load user insights.')
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [workerId, refreshTrigger])

  const handleDeleteLexicon = async (id) => {
    setDeleting(id)
    try {
      await deleteLexicon(workerId, id)
      setLexicons((prev) => prev.filter((l) => l.id !== id))
    } catch {}
    setDeleting(null)
  }

  const emotion = EMOTION_CONFIG[profile?.emotion_state] || EMOTION_CONFIG.neutral
  const EmotionIcon = emotion.icon
  const toneConfig = TONE_LABELS[profile?.tone_preference] || TONE_LABELS.friendly_fun

  const personalityObservations = profile?.personality_notes
    ? profile.personality_notes.split('|').map((s) => s.trim()).filter(Boolean)
    : []

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-gray-400">
        <RefreshCw className="w-5 h-5 animate-spin mr-2" />
        <span className="text-sm">Loading insights...</span>
      </div>
    )
  }

  if (error) {
    return <div className="text-red-500 text-sm text-center py-10">{error}</div>
  }

  return (
    <div className="space-y-5">

      {/* Header card */}
      <div className="bg-gradient-to-br from-indigo-600 to-indigo-700 rounded-2xl p-5 text-white">
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 rounded-full bg-white/20 flex items-center justify-center text-white font-bold text-lg">
            {workerId?.[0]?.toUpperCase() || 'W'}
          </div>
          <div>
            <h2 className="font-bold text-lg">{workerId}</h2>
            <p className="text-indigo-200 text-sm">Worker Insights — What ARIA knows about you</p>
          </div>
        </div>
      </div>

      {!profile ? (
        <EmptyState message={`No profile for "${workerId}" yet — start talking to ARIA to build one!`} />
      ) : (
        <>
          {/* Emotion + Tone row */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">

            {/* Emotion card */}
            <div className={`rounded-2xl border p-4 ${emotion.bg}`}>
              <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">Current Mood</p>
              <div className="flex items-center gap-3">
                <EmotionIcon className={`w-8 h-8 ${emotion.color}`} />
                <div>
                  <p className={`font-bold text-base ${emotion.color}`}>{emotion.label}</p>
                  <p className="text-gray-500 text-xs">{emotion.desc}</p>
                </div>
              </div>
            </div>

            {/* Tone card */}
            <div className={`rounded-2xl border p-4 ${toneConfig.bg}`}>
              <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">ARIA's Tone Mode</p>
              <div className="flex items-center gap-3">
                <Zap className={`w-8 h-8 ${toneConfig.color}`} />
                <div>
                  <p className={`font-bold text-base ${toneConfig.color}`}>{toneConfig.label}</p>
                  <p className="text-gray-500 text-xs">
                    {profile.tone_preference === 'friendly_fun'
                      ? 'Slightly witty replies with personality'
                      : 'Switched to formal — emotion detected'}
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Personality observations */}
          <div className="bg-white rounded-2xl border border-gray-200 p-5">
            <div className="flex items-center gap-2 mb-3">
              <Brain className="w-4 h-4 text-indigo-500" />
              <h3 className="font-semibold text-gray-800 text-sm">ARIA's Observations</h3>
              <span className="text-xs text-gray-400 ml-auto">latest first</span>
            </div>
            {personalityObservations.length === 0 ? (
              <p className="text-gray-400 text-sm italic">No observations recorded yet — keep chatting!</p>
            ) : (
              <div className="space-y-2">
                {personalityObservations.map((obs, i) => (
                  <div key={i} className={`flex items-start gap-3 rounded-xl px-3 py-2.5 ${
                    i === 0
                      ? 'bg-indigo-50 border border-indigo-100'
                      : 'bg-gray-50 border border-gray-100'
                  }`}>
                    <span className={`mt-0.5 text-xs font-bold shrink-0 ${i === 0 ? 'text-indigo-400' : 'text-gray-300'}`}>
                      {i === 0 ? 'NOW' : `#${i + 1}`}
                    </span>
                    <p className={`text-sm leading-relaxed ${i === 0 ? 'text-indigo-800' : 'text-gray-500'}`}>
                      {obs}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {/* Lexicons table */}
      <div className="bg-white rounded-2xl border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <BookOpen className="w-4 h-4 text-indigo-500" />
            <h3 className="font-semibold text-gray-800 text-sm">Personal Vocabulary</h3>
            <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 border border-gray-200">
              {lexicons.length} entries
            </span>
          </div>
          <button onClick={load} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-50 transition-colors">
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>

        {lexicons.length === 0 ? (
          <EmptyState message="No custom vocabulary detected yet — ARIA learns your words as you talk." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-gray-400 border-b border-gray-100">
                  <th className="pb-2 font-medium pr-4">You said</th>
                  <th className="pb-2 font-medium pr-4">Means</th>
                  <th className="pb-2 font-medium pr-4">Type</th>
                  <th className="pb-2 font-medium pr-4 text-center">Used</th>
                  <th className="pb-2 font-medium pr-4">First seen</th>
                  <th className="pb-2 font-medium" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {lexicons.map((lex) => (
                  <tr key={lex.id} className="group hover:bg-gray-50/60 transition-colors">
                    <td className="py-2.5 pr-4">
                      <code className="text-indigo-600 font-mono text-xs bg-indigo-50 px-1.5 py-0.5 rounded">
                        {lex.original_word}
                      </code>
                    </td>
                    <td className="py-2.5 pr-4 text-gray-700 font-medium">
                      {lex.resolved_word || <span className="text-gray-400 italic">—</span>}
                    </td>
                    <td className="py-2.5 pr-4">
                      <span className={`text-xs px-2 py-0.5 rounded-full border capitalize ${WORD_TYPE_COLORS[lex.word_type] || WORD_TYPE_COLORS.unknown}`}>
                        {lex.word_type}
                      </span>
                    </td>
                    <td className="py-2.5 pr-4 text-center text-gray-500 font-mono text-xs">
                      {lex.usage_count}×
                    </td>
                    <td className="py-2.5 pr-4 text-gray-400 text-xs">
                      {new Date(lex.first_seen).toLocaleDateString()}
                    </td>
                    <td className="py-2.5">
                      <button
                        onClick={() => handleDeleteLexicon(lex.id)}
                        disabled={deleting === lex.id}
                        className="opacity-0 group-hover:opacity-100 p-1 rounded text-gray-300 hover:text-red-400 transition-all"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Last updated */}
      {profile?.updated_at && (
        <p className="text-center text-xs text-gray-400">
          Profile last updated: {new Date(profile.updated_at).toLocaleString()}
        </p>
      )}
    </div>
  )
}
