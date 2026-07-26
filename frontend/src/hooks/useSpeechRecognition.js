import { useState, useRef, useCallback, useEffect } from 'react'

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition

export function useSpeechRecognition({ onResult, onError } = {}) {
  const [isListening, setIsListening] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [isSupported] = useState(!!SpeechRecognition)
  const recognitionRef = useRef(null)
  const silenceTimerRef = useRef(null)
  const finalTextRef = useRef('')
  const onResultRef = useRef(onResult)
  const onErrorRef = useRef(onError)
  // Tracks whether the user explicitly wants to be listening.
  // Chrome fires onend unexpectedly (no-speech timeout, audio interference, etc.)
  // even with continuous=true — we restart in that case instead of going silent.
  const intentionalRef = useRef(false)

  useEffect(() => { onResultRef.current = onResult }, [onResult])
  useEffect(() => { onErrorRef.current = onError }, [onError])

  useEffect(() => {
    if (!isSupported) return
    const recognition = new SpeechRecognition()
    recognition.continuous = true
    recognition.interimResults = true
    recognition.lang = 'en-US'
    recognition.maxAlternatives = 1

    recognition.onresult = (event) => {
      let interim = ''
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const r = event.results[i]
        if (r.isFinal) {
          finalTextRef.current += r[0].transcript
          // Reset 2s silence timer on each finalized word
          clearTimeout(silenceTimerRef.current)
          silenceTimerRef.current = setTimeout(() => {
            const text = finalTextRef.current.trim()
            if (text) {
              onResultRef.current?.(text)
              finalTextRef.current = ''
              setTranscript('')
              intentionalRef.current = false
              recognition.stop()
            }
          }, 2000)
        } else {
          interim += r[0].transcript
        }
      }
      setTranscript(finalTextRef.current + interim)
    }

    recognition.onerror = (event) => {
      // no-speech is normal in continuous mode — Chrome will fire onend after
      // this, which we handle by restarting if intentionalRef is still true.
      if (event.error === 'no-speech') return
      intentionalRef.current = false
      clearTimeout(silenceTimerRef.current)
      setIsListening(false)
      onErrorRef.current?.(event.error)
    }

    recognition.onend = () => {
      clearTimeout(silenceTimerRef.current)
      if (intentionalRef.current) {
        // Chrome ended recognition unexpectedly — restart to keep mic active
        try { recognition.start() } catch { /* already started */ }
        return
      }
      setIsListening(false)
    }

    recognitionRef.current = recognition
    return () => {
      intentionalRef.current = false
      clearTimeout(silenceTimerRef.current)
      recognition.abort()
    }
  }, [isSupported])

  const startListening = useCallback(() => {
    if (!recognitionRef.current || isListening) return
    finalTextRef.current = ''
    setTranscript('')
    intentionalRef.current = true
    recognitionRef.current.start()
    setIsListening(true)
  }, [isListening])

  const stopListening = useCallback(() => {
    intentionalRef.current = false
    clearTimeout(silenceTimerRef.current)
    finalTextRef.current = ''
    recognitionRef.current?.stop()
    setIsListening(false)
  }, [])

  return { isListening, transcript, isSupported, startListening, stopListening }
}

// Text-to-speech was removed along with the backend's /api/voice/synthesize
// endpoint (Claude has no TTS API). Voice input above is unaffected — it's
// the browser's native Web Speech API, no backend call involved.
