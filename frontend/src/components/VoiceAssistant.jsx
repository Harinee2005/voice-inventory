import { useState, useRef, useCallback, useEffect } from 'react'
import { Mic, MicOff, Send, RotateCcw, Volume2, VolumeX, MapPin } from 'lucide-react'
import WaveformAnimation from './WaveformAnimation'
import { useSpeechRecognition, useTTS } from '../hooks/useSpeechRecognition'
import { processVoiceText, clearConversation } from '../services/api'

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

export default function VoiceAssistant({ sessionId, workerId, onInventoryUpdate, storageArea, locationName, shouldGreet = true, onGreeted }) {
  const greeting = buildGreeting(workerId, locationName, storageArea)

  const [messages, setMessages] = useState([
    { id: 1, role: 'assistant', text: greeting, action: 'none', flags: [] },
  ])
  const [inputText, setInputText] = useState('')
  const [isProcessing, setIsProcessing] = useState(false)
  const [muteVoice, setMuteVoice] = useState(false)
  const messagesEndRef = useRef(null)
  const { isSpeaking, speak, stopSpeaking } = useTTS()

  useEffect(() => {
    if (!shouldGreet) return
    const timer = setTimeout(() => {
      speak(greeting)
      onGreeted?.()
    }, 800)
    return () => clearTimeout(timer)
  }, [])

  const scrollToBottom = () => messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })

  const addMessage = (role, text, action = 'none', flags = [], data = null) => {
    setMessages((prev) => [...prev, { id: Date.now(), role, text, action, flags, data }])
    setTimeout(scrollToBottom, 100)
  }

  const handleSend = useCallback(
    async (text) => {
      const trimmed = text?.trim()
      if (!trimmed || isProcessing) return
      setInputText('')
      addMessage('user', trimmed)
      setIsProcessing(true)
      try {
        const { data: res } = await processVoiceText(trimmed, sessionId, workerId, storageArea, locationName)
        const flags = res.data?.flags || []
        addMessage('assistant', res.message, res.action, flags, res.data)
        if (!muteVoice) speak(res.message, res.audio_base64 || null)
        if (res.inventory_updated) onInventoryUpdate?.()
      } catch (err) {
        const isTimeout = err.code === 'ECONNABORTED' || err.message?.includes('timeout')
        const errMsg = isTimeout
          ? 'The AI took too long to respond — please try again.'
          : 'Sorry, I encountered an error. Please check if the backend is running.'
        addMessage('assistant', errMsg, 'none')
        if (!muteVoice) speak(errMsg)
      } finally {
        setIsProcessing(false)
      }
    },
    [sessionId, workerId, isProcessing, muteVoice, speak, onInventoryUpdate, storageArea, locationName]
  )

  const { isListening, transcript, isSupported, startListening, stopListening } =
    useSpeechRecognition({ onResult: handleSend })

  const handleMicToggle = () => {
    if (isSpeaking) stopSpeaking()
    if (isListening) stopListening()
    else startListening()
  }

  const handleClear = async () => {
    await clearConversation(sessionId)
    setMessages([{ id: Date.now(), role: 'assistant', text: 'Conversation cleared. Ready for a fresh start!', action: 'none', flags: [] }])
  }

  const isActive = isListening || isSpeaking || isProcessing

  const statusLabel = isListening ? 'Listening...' : isProcessing ? 'Thinking...' : isSpeaking ? 'Speaking...' : 'Ready'
  const statusColor = isListening ? 'bg-blue-500' : isProcessing ? 'bg-amber-400' : isSpeaking ? 'bg-purple-500' : 'bg-emerald-500'

  return (
    <div className="flex flex-col h-full bg-white rounded-2xl border border-gray-200 overflow-hidden shadow-sm">
      {/* Header */}
      <div className="px-5 py-3.5 border-b border-gray-100 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={`w-2 h-2 rounded-full ${statusColor} ${isActive ? 'status-dot-active' : ''}`} />
          <span className="text-gray-800 font-semibold text-sm">Voice Assistant</span>
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            isListening ? 'bg-blue-50 text-blue-600 border border-blue-100'
            : isProcessing ? 'bg-amber-50 text-amber-600 border border-amber-100'
            : isSpeaking ? 'bg-purple-50 text-purple-600 border border-purple-100'
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
            onClick={() => setMuteVoice((v) => !v)}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-50 transition-colors"
          >
            {muteVoice ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
          </button>
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
              </div>
            ) : (
              <div className="max-w-[75%] px-4 py-3 rounded-xl bg-indigo-600 text-white">
                <p className="text-sm">{msg.text}</p>
              </div>
            )}
          </div>
        ))}

        {isProcessing && (
          <div className="flex justify-start animate-slide-in">
            <div className="px-4 py-3 rounded-xl border border-gray-100 bg-gray-50">
              <div className="flex items-center gap-2">
                <div className="thinking-dot" />
                <div className="thinking-dot" />
                <div className="thinking-dot" />
                <span className="text-gray-400 text-xs ml-1">ARIA is thinking</span>
              </div>
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
  )
}
