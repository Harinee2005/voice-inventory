import { useState, useRef, useCallback, useEffect, Fragment } from 'react'
import { Mic, MicOff, Send, RotateCcw, MapPin, ChevronDown, ChevronUp, X, Activity } from 'lucide-react'
import WaveformAnimation from './WaveformAnimation'
import { useSpeechRecognition } from '../hooks/useSpeechRecognition'
import { streamVoiceText, clearConversation } from '../services/api'

// ── KV grid helpers (shared between inline + panel) ──────────────────────────

function PipelineKVVal({ v, depth }) {
  const d = depth || 0
  if (v === null || v === undefined || v === '') return null
  if (typeof v === 'boolean') return (
    <span className={v ? 'text-emerald-700 font-semibold' : 'text-gray-400'}>{v ? 'yes' : 'no'}</span>
  )
  if (Array.isArray(v)) {
    if (!v.length) return null
    if (v.every(x => typeof x !== 'object' || x === null)) {
      return (
        <div className="flex flex-wrap gap-1 mt-0.5">
          {v.map((x, i) => (
            <span key={i} className="inline-flex items-center px-1.5 py-0.5 rounded bg-gray-100 text-gray-700 text-xs leading-tight">{String(x)}</span>
          ))}
        </div>
      )
    }
    return (
      <div className="mt-1 space-y-2">
        {v.map((x, i) => (
          <div key={i} className="border-l-2 border-indigo-200 pl-2.5 py-0.5">
            {x && typeof x === 'object'
              ? <PipelineKVGrid obj={x} depth={d + 1} />
              : <span className="text-xs text-gray-700">{String(x)}</span>
            }
          </div>
        ))}
      </div>
    )
  }
  if (v && typeof v === 'object') return <PipelineKVGrid obj={v} depth={d + 1} />
  return <span className="text-gray-800 break-words leading-relaxed">{String(v)}</span>
}

function PipelineKVGrid({ obj, depth }) {
  const d = depth || 0
  const entries = Object.entries(obj).filter(([, v]) =>
    v !== null && v !== undefined && v !== '' &&
    !(Array.isArray(v) && !v.length) &&
    !(v && typeof v === 'object' && !Array.isArray(v) && !Object.keys(v).length)
  )
  if (!entries.length) return null
  return (
    <div
      className={d > 0 ? 'ml-1' : ''}
      style={{ display: 'grid', gridTemplateColumns: 'max-content 1fr', columnGap: '12px', rowGap: '6px' }}
    >
      {entries.map(([k, v]) => (
        <Fragment key={k}>
          <span className="text-xs text-gray-400 font-medium capitalize whitespace-nowrap leading-relaxed self-start pt-0.5">
            {k.replace(/_/g, ' ')}
          </span>
          <div className="text-xs text-gray-800 min-w-0 leading-relaxed">
            <PipelineKVVal v={v} depth={d} />
          </div>
        </Fragment>
      ))}
    </div>
  )
}

// ── Side Panel ────────────────────────────────────────────────────────────────

function PanelStep({ step }) {
  const [expanded, setExpanded] = useState(true)
  const hasDetail = (step.input && Object.keys(step.input).length) ||
    step.reasoning ||
    (step.output && Object.keys(step.output).length)

  return (
    <div className="border-b border-gray-100 last:border-0">
      <button
        onClick={() => hasDetail && setExpanded(e => !e)}
        className={`w-full flex items-center gap-3 px-5 py-3.5 text-left transition-colors ${hasDetail ? 'hover:bg-gray-50 cursor-pointer' : 'cursor-default'}`}
      >
        <span className="text-xl shrink-0 leading-none">{step.icon}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-xs font-semibold text-gray-700">{step.label}</span>
          </div>
          <div className="text-xs text-gray-500 leading-relaxed">{step.detail}</div>
        </div>
        {hasDetail && (
          expanded
            ? <ChevronUp className="w-4 h-4 text-gray-300 shrink-0" />
            : <ChevronDown className="w-4 h-4 text-gray-300 shrink-0" />
        )}
      </button>

      {expanded && hasDetail && (
        <div className="px-5 pb-5 pt-1 space-y-4 bg-gray-50/50 border-t border-gray-100">
          {step.input && Object.keys(step.input).length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-widest font-bold text-indigo-500 mb-2">Input</div>
              <PipelineKVGrid obj={step.input} />
            </div>
          )}
          {step.reasoning && (
            <div>
              <div className="text-[10px] uppercase tracking-widest font-bold text-amber-500 mb-2">Reasoning</div>
              <div className="text-xs text-gray-600 italic bg-amber-50 rounded-lg px-3 py-2.5 leading-relaxed border border-amber-100">
                {step.reasoning}
              </div>
            </div>
          )}
          {step.output && Object.keys(step.output).length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-widest font-bold text-emerald-600 mb-2">Output</div>
              <PipelineKVGrid obj={step.output} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ThinkingPanel({ open, isLive, turnId, liveSteps, messages, onClose }) {
  if (!open) return null

  const turnMsg = messages.find(m => m.id === turnId)
  const steps = isLive ? liveSteps : (turnMsg?.trace || [])
  const turnLabel = isLive
    ? 'Processing...'
    : turnMsg?.userText || 'Pipeline trace'

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/25 z-40 backdrop-blur-[2px]"
        onClick={onClose}
      />
      {/* Drawer */}
      <div className="fixed right-0 top-0 h-full w-[460px] max-w-[95vw] bg-white z-50 shadow-2xl flex flex-col border-l border-gray-200">
        {/* Header */}
        <div className="px-5 py-4 border-b border-gray-100 flex items-start gap-3 shrink-0">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <div className={`text-[10px] uppercase tracking-widest font-bold ${isLive ? 'text-amber-500' : 'text-indigo-500'}`}>
                {isLive ? '● Live' : 'Pipeline Trace'}
              </div>
              {!isLive && steps.length > 0 && (
                <span className="text-[10px] text-gray-400 font-medium">{steps.length} steps</span>
              )}
            </div>
            <div className="text-sm font-semibold text-gray-700 leading-snug line-clamp-2">{turnLabel}</div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors shrink-0 mt-0.5"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Legend row */}
        <div className="px-5 py-2 border-b border-gray-100 flex items-center gap-4 shrink-0 bg-gray-50/60">
          {[
            { dot: 'bg-indigo-400', label: 'Input' },
            { dot: 'bg-amber-400', label: 'Reasoning' },
            { dot: 'bg-emerald-400', label: 'Output' },
          ].map(({ dot, label }) => (
            <div key={label} className="flex items-center gap-1.5">
              <div className={`w-2 h-2 rounded-full ${dot}`} />
              <span className="text-[10px] text-gray-500 font-medium">{label}</span>
            </div>
          ))}
        </div>

        {/* Steps list */}
        <div className="flex-1 overflow-y-auto">
          {steps.length === 0 ? (
            <div className="flex items-center gap-2 px-5 py-8 text-gray-400 text-sm">
              {isLive ? (
                <>
                  <div className="thinking-dot" /><div className="thinking-dot" /><div className="thinking-dot" />
                  <span className="ml-1">Starting…</span>
                </>
              ) : (
                <span>No trace available.</span>
              )}
            </div>
          ) : (
            steps.map((step, i) => (
              <PanelStep key={(step.step ?? i) + String(isLive)} step={step} />
            ))
          )}
        </div>
      </div>
    </>
  )
}

// ── Message helpers ───────────────────────────────────────────────────────────

const ACTION_COLORS = {
  confirm:      'border-l-amber-400 bg-amber-50/60',
  update:       'border-l-emerald-400 bg-emerald-50/60',
  clarify:      'border-l-blue-400 bg-blue-50/60',
  flag:         'border-l-red-400 bg-red-50/60',
  query_result: 'border-l-purple-400 bg-purple-50/60',
  none:         'border-l-gray-200 bg-white',
}

const FLAG_BADGES = {
  unit_mismatch:      { label: 'Unit Mismatch',       color: 'bg-orange-50 text-orange-600 border-orange-200' },
  suspicious_quantity:{ label: 'Unusual Qty',          color: 'bg-red-50 text-red-600 border-red-200' },
  conflict:           { label: 'Conflict',             color: 'bg-red-50 text-red-600 border-red-200' },
  incomplete:         { label: 'Needs Clarification',  color: 'bg-amber-50 text-amber-600 border-amber-200' },
  expiry_warning:     { label: 'Expiry Alert',         color: 'bg-orange-50 text-orange-600 border-orange-200' },
  storage_warning:    { label: 'Storage Mismatch',     color: 'bg-yellow-50 text-yellow-700 border-yellow-200' },
}

function buildGreeting(workerId, locationName, storageArea) {
  const hour = new Date().getHours()
  const timeOfDay = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
  const name = workerId?.replace(/_/g, ' ') || 'there'
  const workspace = locationName && storageArea
    ? ` You're counting inventory for ${storageArea} at ${locationName}.`
    : ''
  return `${timeOfDay}, ${name}! I'm ARIA, your inventory assistant.${workspace} Tell me what you're counting today, or ask me anything about the current stock.`
}

// ── Main component ────────────────────────────────────────────────────────────

export default function VoiceAssistant({ sessionId, workerId, onInventoryUpdate, storageArea, locationName, shouldGreet = true, onGreeted }) {
  const greeting = buildGreeting(workerId, locationName, storageArea)

  const [messages, setMessages] = useState([
    { id: 1, role: 'assistant', text: greeting, action: 'none', flags: [], trace: [], userText: '' },
  ])
  const [inputText, setInputText] = useState('')
  const [isProcessing, setIsProcessing] = useState(false)
  const [thinkingSteps, setThinkingSteps] = useState([])
  const [streamingText, setStreamingText] = useState('')
  const [sidePanel, setSidePanel] = useState({ open: false, turnId: null, isLive: false })
  const thinkingTraceRef = useRef([])
  const pendingUserTextRef = useRef('')
  const messagesEndRef = useRef(null)

  useEffect(() => {
    if (!shouldGreet) return
    const timer = setTimeout(() => {
      onGreeted?.()
    }, 800)
    return () => clearTimeout(timer)
  }, [])

  // Keep live panel updated when processing
  useEffect(() => {
    if (sidePanel.open && sidePanel.isLive && !isProcessing) {
      // Keep panel open but mark it as no longer live (will show final trace when msg arrives)
    }
  }, [isProcessing])

  const scrollToBottom = () => messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })

  const addMessage = (role, text, action = 'none', flags = [], data = null, trace = [], userText = '') => {
    setMessages((prev) => [...prev, { id: Date.now(), role, text, action, flags, data, trace, userText }])
    setTimeout(scrollToBottom, 100)
  }

  const openLivePanel = useCallback(() => {
    setSidePanel({ open: true, turnId: null, isLive: true })
  }, [])

  const openTracePanel = useCallback((msgId) => {
    setSidePanel({ open: true, turnId: msgId, isLive: false })
  }, [])

  const closePanel = useCallback(() => {
    setSidePanel({ open: false, turnId: null, isLive: false })
  }, [])

  const handleSend = useCallback(
    async (text) => {
      const trimmed = text?.trim()
      if (!trimmed || isProcessing) return
      setInputText('')
      pendingUserTextRef.current = trimmed
      addMessage('user', trimmed)
      setIsProcessing(true)
      setThinkingSteps([])
      setStreamingText('')

      try {
        await streamVoiceText(
          trimmed, sessionId, workerId, storageArea, locationName, messages,
          (eventType, data) => {
            if (eventType === 'status') {
              setThinkingSteps(prev => {
                const idx = prev.findIndex(s => s.step === data.step)
                let next
                if (idx >= 0) {
                  next = [...prev]; next[idx] = data
                } else {
                  next = [...prev, data]
                }
                thinkingTraceRef.current = next
                return next
              })
              setTimeout(scrollToBottom, 50)
            } else if (eventType === 'chunk') {
              setStreamingText(prev => prev + data.text)
              setTimeout(scrollToBottom, 50)
            } else if (eventType === 'done') {
              const capturedTrace = [...thinkingTraceRef.current]
              const capturedUserText = pendingUserTextRef.current
              thinkingTraceRef.current = []
              pendingUserTextRef.current = ''
              setThinkingSteps([])
              setStreamingText('')
              const flags = data.data?.flags || []
              const newMsgId = Date.now()
              setMessages((prev) => [...prev, {
                id: newMsgId,
                role: 'assistant',
                text: data.message,
                action: data.action,
                flags,
                data: data.data,
                trace: capturedTrace,
                userText: capturedUserText,
              }])
              // If panel was live, switch it to show this message's trace
              setSidePanel(prev => prev.open && prev.isLive
                ? { open: true, turnId: newMsgId, isLive: false }
                : prev
              )
              setTimeout(scrollToBottom, 100)
              if (data.inventory_updated) onInventoryUpdate?.()
            } else if (eventType === 'error') {
              thinkingTraceRef.current = []
              pendingUserTextRef.current = ''
              setThinkingSteps([])
              setStreamingText('')
              addMessage('assistant', data.message || 'Something went wrong.', 'none')
            }
          }
        )
      } catch (err) {
        const errMsg = 'Sorry, I encountered an error. Please check if the backend is running.'
        thinkingTraceRef.current = []
        pendingUserTextRef.current = ''
        setThinkingSteps([])
        setStreamingText('')
        addMessage('assistant', errMsg, 'none')
      } finally {
        thinkingTraceRef.current = []
        pendingUserTextRef.current = ''
        setIsProcessing(false)
        setThinkingSteps([])
        setStreamingText('')
      }
    },
    [messages, sessionId, workerId, isProcessing, onInventoryUpdate, storageArea, locationName]
  )

  const { isListening, transcript, isSupported, startListening, stopListening } =
    useSpeechRecognition({ onResult: handleSend })

  const handleMicToggle = () => {
    if (isListening) stopListening()
    else startListening()
  }

  const handleClear = async () => {
    await clearConversation(sessionId)
    setMessages([{ id: Date.now(), role: 'assistant', text: 'Conversation cleared. Ready for a fresh start!', action: 'none', flags: [], trace: [], userText: '' }])
  }

  const isActive = isListening || isProcessing
  const statusLabel = isListening ? 'Listening...' : isProcessing ? 'Thinking...' : 'Ready'
  const statusColor = isListening ? 'bg-blue-500' : isProcessing ? 'bg-amber-400' : 'bg-emerald-500'

  // liveSteps for the panel: current thinkingSteps while processing
  const liveStepsForPanel = thinkingSteps

  return (
    <>
      <div className="flex flex-col h-full bg-white rounded-2xl border border-gray-200 overflow-hidden shadow-sm">
        {/* Header */}
        <div className="px-5 py-3.5 border-b border-gray-100 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className={`w-2 h-2 rounded-full ${statusColor} ${isActive ? 'status-dot-active' : ''}`} />
            <span className="text-gray-800 font-semibold text-sm">Voice Assistant</span>
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
              isListening ? 'bg-blue-50 text-blue-600 border border-blue-100'
              : isProcessing ? 'bg-amber-50 text-amber-600 border border-amber-100'
              : 'bg-gray-50 text-gray-500 border border-gray-200'
            }`}>
              {statusLabel}
            </span>
            {storageArea && (
              <span className="hidden sm:flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-gray-50 text-gray-400 border border-gray-200">
                <MapPin className="w-2.5 h-2.5" />
                {storageArea}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <button
              onClick={handleClear}
              className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-50 transition-colors"
            >
              <RotateCcw className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Waveform */}
        <div className="px-5 py-2.5 border-b border-gray-100 bg-gray-50/40">
          <WaveformAnimation isActive={isActive} barCount={32} />
          {transcript && (
            <p className="text-center text-indigo-500 text-xs mt-1 italic animate-pulse">"{transcript}"</p>
          )}
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 min-h-0">
          {messages.map((msg) => (
            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'} animate-slide-in`}>
              {msg.role === 'assistant' ? (
                <div className={`max-w-[88%] rounded-xl border-l-4 px-4 py-3 border border-gray-100 ${ACTION_COLORS[msg.action] || ACTION_COLORS.none}`}>
                  <div className="flex items-center gap-2 mb-1.5">
                    <div className="w-5 h-5 rounded-full bg-indigo-600 flex items-center justify-center text-[9px] font-bold text-white shrink-0">
                      AI
                    </div>
                    <span className="text-gray-400 text-xs">ARIA</span>
                    {msg.action !== 'none' && (
                      <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${
                        msg.action === 'confirm'  ? 'bg-amber-50 text-amber-600 border-amber-200'
                        : msg.action === 'update'  ? 'bg-emerald-50 text-emerald-600 border-emerald-200'
                        : msg.action === 'flag'    ? 'bg-red-50 text-red-600 border-red-200'
                        : 'bg-blue-50 text-blue-600 border-blue-200'
                      }`}>
                        {msg.action}
                      </span>
                    )}
                  </div>
                  <p className="text-gray-700 text-sm leading-relaxed">{msg.text}</p>

                  {/* Flag badges */}
                  {msg.flags?.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-2">
                      {msg.flags.map((flag) => {
                        const badge = FLAG_BADGES[flag]
                        return badge ? (
                          <span key={flag} className={`text-xs px-2 py-0.5 rounded-full border ${badge.color}`}>
                            ⚠ {badge.label}
                          </span>
                        ) : null
                      })}
                    </div>
                  )}

                  {/* Item preview on confirm */}
                  {msg.action === 'confirm' && (() => {
                    const items = msg.data?.items?.length
                      ? msg.data.items
                      : msg.data?.item_name
                      ? [{ item_name: msg.data.item_name, quantity: msg.data.quantity, unit: msg.data.unit, storage_area: msg.data.storage_area, category: msg.data.category }]
                      : []
                    if (!items.length) return null
                    return (
                      <div className="mt-2 rounded-lg bg-white border border-gray-100 overflow-hidden">
                        {items.map((item, idx) => (
                          <div key={idx} className={`px-3 py-2 grid grid-cols-2 gap-x-4 gap-y-0.5 ${idx > 0 ? 'border-t border-gray-100' : ''}`}>
                            {[
                              ['Item', item.item_name],
                              ['Qty', `${item.quantity ?? '?'} ${item.unit ?? ''}`],
                              ['Area', item.storage_area],
                              ['Category', item.category],
                            ].filter(([, v]) => v).map(([k, v]) => (
                              <div key={k} className="flex items-center gap-1">
                                <span className="text-gray-400 text-xs">{k}:</span>
                                <span className="text-gray-600 text-xs capitalize">{v}</span>
                              </div>
                            ))}
                          </div>
                        ))}
                      </div>
                    )
                  })()}

                  {/* Thinking trace button */}
                  {msg.trace?.length > 0 && (
                    <button
                      onClick={() => openTracePanel(msg.id)}
                      className="flex items-center gap-1.5 mt-2.5 text-xs text-indigo-500 hover:text-indigo-700 transition-colors font-medium group"
                    >
                      <Activity className="w-3.5 h-3.5 group-hover:scale-110 transition-transform" />
                      <span>View thinking · {msg.trace.length} steps</span>
                    </button>
                  )}
                </div>
              ) : (
                <div className="max-w-[75%] px-4 py-3 rounded-xl bg-indigo-600 text-white">
                  <p className="text-sm">{msg.text}</p>
                </div>
              )}
            </div>
          ))}

          {/* Live processing bubble */}
          {isProcessing && (
            <div className="flex justify-start animate-slide-in">
              <div className="max-w-[88%] rounded-xl border border-gray-100 bg-gray-50/80 px-4 py-3 space-y-2">

                {/* Live step summary (compact) */}
                {thinkingSteps.length > 0 && (
                  <div className="divide-y divide-gray-100 -mx-1">
                    {thinkingSteps.map((step, i) => (
                      <div key={step.step ?? i} className="px-1 py-1.5 flex items-center gap-2.5">
                        <span className="text-sm shrink-0">{step.icon}</span>
                        <span className="text-xs font-semibold text-gray-700 w-[60px] shrink-0">{step.label}</span>
                        <span className="text-xs text-gray-500 flex-1 truncate">{step.detail}</span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Open thinking panel button */}
                <button
                  onClick={openLivePanel}
                  className="flex items-center gap-1.5 text-xs text-indigo-500 hover:text-indigo-700 transition-colors font-medium pt-1"
                >
                  <Activity className="w-3.5 h-3.5 animate-pulse" />
                  <span>{thinkingSteps.length > 0 ? 'Open thinking panel →' : 'ARIA is thinking…'}</span>
                </button>

                {/* Streaming text */}
                {streamingText && (
                  <p className="text-gray-700 text-sm leading-relaxed">
                    {streamingText}<span className="animate-pulse text-indigo-400">▋</span>
                  </p>
                )}
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="px-4 py-4 border-t border-gray-100 bg-gray-50/40">
          <div className="flex items-center gap-3">
            <button
              onClick={handleMicToggle}
              disabled={!isSupported}
              className={`relative w-11 h-11 rounded-full flex items-center justify-center transition-all duration-200 shrink-0 ${
                isListening
                  ? 'bg-indigo-600 mic-glow'
                  : 'bg-white border border-gray-200 hover:border-indigo-300 hover:bg-indigo-50'
              } ${!isSupported ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}`}
            >
              {isListening
                ? <MicOff className="w-4.5 h-4.5 text-white" />
                : <Mic className="w-4.5 h-4.5 text-indigo-500" />}
            </button>

            <div className="flex-1">
              <input
                type="text"
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend(inputText)}
                placeholder="Type or speak your inventory update..."
                disabled={isProcessing}
                className="w-full px-4 py-2.5 rounded-xl bg-white border border-gray-200 text-gray-700 placeholder-gray-300 text-sm outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-50 transition-colors disabled:opacity-60"
              />
            </div>

            <button
              onClick={() => handleSend(inputText)}
              disabled={!inputText.trim() || isProcessing}
              className="w-11 h-11 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-100 disabled:opacity-40 flex items-center justify-center transition-all duration-200 shrink-0"
            >
              <Send className="w-4 h-4 text-white" />
            </button>
          </div>

          {!isSupported && (
            <p className="text-gray-400 text-xs mt-2 text-center">
              Voice input unavailable — use Chrome or Edge for full experience
            </p>
          )}
        </div>
      </div>

      {/* Side Panel — rendered outside chat card so it overlays full screen */}
      <ThinkingPanel
        open={sidePanel.open}
        isLive={sidePanel.isLive}
        turnId={sidePanel.turnId}
        liveSteps={liveStepsForPanel}
        messages={messages}
        onClose={closePanel}
      />
    </>
  )
}
