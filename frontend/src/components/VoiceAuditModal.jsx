import { useCallback, useEffect, useRef, useState } from 'react'
import { Mic, MicOff, MapPin, Package, X } from 'lucide-react'
import { useSpeechRecognition, useTTS } from '../hooks/useSpeechRecognition'
import { processVoiceText } from '../services/api'

function buildGreeting(workerId, locationName, storageArea) {
  const hour = new Date().getHours()
  const time = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
  const name = workerId?.replace(/_/g, ' ') || 'there'
  const workspace = locationName && storageArea
    ? ` You're counting inventory for ${storageArea} at ${locationName}.`
    : ''
  return `${time}, ${name}! I'm ARIA, your inventory assistant.${workspace} Go ahead and tell me what you're counting today.`
}

export default function VoiceAuditModal({ isOpen, onClose, workspace, sessionId, workerId, onInventoryUpdate, greetOnOpen = false }) {
  const [status, setStatus] = useState('idle')   // idle | greeting | listening | processing | result
  const [transcript, setTranscript] = useState('')
  const [result, setResult] = useState(null)
  const { speak, isSpeaking } = useTTS()

  const handleResult = useCallback(async (text) => {
    if (!text?.trim()) return
    setTranscript(text.trim())
    setStatus('processing')

    try {
      const { data: res } = await processVoiceText(
        text.trim(), sessionId, workerId,
        workspace?.storageAreaName, workspace?.locationName
      )
      setResult(res)
      setStatus('result')
      speak(res.message)
      if (res.inventory_updated) onInventoryUpdate?.()
    } catch {
      setResult({ message: 'Something went wrong. Please try again.', action: 'none' })
      setStatus('result')
    }
  }, [sessionId, workerId, workspace, speak, onInventoryUpdate])

  const { isListening, startListening, stopListening, isSupported } =
    useSpeechRecognition({ onResult: handleResult })

  useEffect(() => {
    if (!isOpen) {
      stopListening()
      setStatus('idle')
      setTranscript('')
      setResult(null)
      return
    }

    if (greetOnOpen) {
      // Speak greeting first, then start listening after speech ends
      setStatus('greeting')
      const greeting = buildGreeting(workerId, workspace?.locationName, workspace?.storageAreaName)
      speak(greeting)
      // Poll for speech to finish, then start listening
      const poll = setInterval(() => {
        // isSpeaking might still be false at first tick — give a minimum delay
      }, 200)
      const timer = setTimeout(() => {
        clearInterval(poll)
        startListening()
        setStatus('listening')
      }, 4000) // ~4s covers most greetings
      return () => { clearInterval(poll); clearTimeout(timer) }
    } else {
      const timer = setTimeout(() => {
        startListening()
        setStatus('listening')
      }, 300)
      return () => clearTimeout(timer)
    }
  }, [isOpen])

  const handleClose = () => {
    stopListening()
    onClose()
  }

  const handleRetry = () => {
    setStatus('listening')
    setTranscript('')
    setResult(null)
    startListening()
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl w-full max-w-sm shadow-2xl overflow-hidden">
        {/* Close */}
        <div className="flex justify-end p-4 pb-0">
          <button onClick={handleClose} className="p-1.5 rounded-lg text-gray-300 hover:text-gray-500 hover:bg-gray-50 transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-8 pb-8 pt-2 text-center">
          {/* Mic button with pulse rings */}
          <div className="relative flex items-center justify-center mb-6">
            {status === 'listening' && (
              <>
                <div className="absolute w-28 h-28 rounded-full bg-indigo-100 animate-ping opacity-40" />
                <div className="absolute w-24 h-24 rounded-full bg-indigo-100 opacity-60" />
              </>
            )}
            <button
              onClick={isListening ? () => { stopListening(); setStatus('idle') } : handleRetry}
              disabled={status === 'processing'}
              className={`relative w-20 h-20 rounded-full flex items-center justify-center transition-all duration-300 ${
                status === 'listening'
                  ? 'bg-indigo-600 shadow-lg shadow-indigo-200'
                  : status === 'processing'
                  ? 'bg-indigo-400'
                  : 'bg-indigo-100 hover:bg-indigo-600 group'
              }`}
            >
              {status === 'listening'
                ? <MicOff className="w-8 h-8 text-white" />
                : <Mic className={`w-8 h-8 ${status === 'processing' ? 'text-white' : 'text-indigo-600 group-hover:text-white'}`} />}
            </button>
          </div>

          {/* Status title */}
          <h2 className="text-2xl font-bold text-gray-900 mb-3">
            {status === 'greeting'   && 'ARIA is greeting you...'}
            {status === 'listening'  && 'Listening...'}
            {status === 'processing' && 'Processing...'}
            {status === 'result'     && 'Done!'}
            {status === 'idle'       && 'Tap to speak'}
          </h2>

          {/* Workspace badges */}
          {workspace && (
            <div className="flex justify-center gap-2 flex-wrap mb-4">
              <span className="flex items-center gap-1 text-xs px-2.5 py-1 rounded-full bg-indigo-50 text-indigo-600 font-medium border border-indigo-100">
                <MapPin className="w-3 h-3" />
                {workspace.locationName?.toUpperCase()}
              </span>
              <span className="flex items-center gap-1 text-xs px-2.5 py-1 rounded-full bg-indigo-50 text-indigo-600 font-medium border border-indigo-100">
                <Package className="w-3 h-3" />
                {workspace.storageAreaName?.toUpperCase()}
              </span>
            </div>
          )}

          {/* Transcript */}
          {transcript && (
            <p className="text-gray-500 text-sm italic mb-3 leading-relaxed">
              "{transcript}"
            </p>
          )}

          {/* ARIA result */}
          {status === 'result' && result && (
            <div className={`mt-2 mb-4 px-4 py-3 rounded-xl text-left text-sm ${
              result.action === 'update'  ? 'bg-emerald-50 border border-emerald-100 text-emerald-700'
              : result.action === 'confirm' ? 'bg-amber-50 border border-amber-100 text-amber-700'
              : result.action === 'clarify' ? 'bg-blue-50 border border-blue-100 text-blue-700'
              : 'bg-gray-50 border border-gray-100 text-gray-600'
            }`}>
              {result.message}
            </div>
          )}

          {/* Hint / action */}
          {status === 'greeting' && (
            <p className="text-indigo-400 text-sm italic">Preparing your session...</p>
          )}
          {status === 'listening' && (
            <p className="text-indigo-400 text-sm italic">Speak clearly into the microphone...</p>
          )}
          {status === 'processing' && (
            <p className="text-gray-400 text-sm">ARIA is processing your request...</p>
          )}

          {/* Buttons */}
          <div className="mt-6 space-y-2">
            {status === 'result' && (
              <button
                onClick={handleRetry}
                className="w-full py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold transition-colors"
              >
                Record Another
              </button>
            )}
            <button
              onClick={handleClose}
              className="w-full text-center text-gray-400 text-sm py-1 hover:text-gray-600 transition-colors"
            >
              Cancel Session
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
