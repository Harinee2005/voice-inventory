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
      // no-speech is normal in continuous mode — just keep listening
      if (event.error === 'no-speech') return
      clearTimeout(silenceTimerRef.current)
      setIsListening(false)
      onErrorRef.current?.(event.error)
    }

    recognition.onend = () => {
      clearTimeout(silenceTimerRef.current)
      setIsListening(false)
    }

    recognitionRef.current = recognition
    return () => {
      clearTimeout(silenceTimerRef.current)
      recognition.abort()
    }
  }, [isSupported])

  const startListening = useCallback(() => {
    if (!recognitionRef.current || isListening) return
    finalTextRef.current = ''
    setTranscript('')
    recognitionRef.current.start()
    setIsListening(true)
  }, [isListening])

  const stopListening = useCallback(() => {
    clearTimeout(silenceTimerRef.current)
    finalTextRef.current = ''
    recognitionRef.current?.stop()
    setIsListening(false)
  }, [])

  return { isListening, transcript, isSupported, startListening, stopListening }
}

export function useTTS() {
  const [isSpeaking, setIsSpeaking] = useState(false)

  const speak = useCallback((text) => {
    if (!window.speechSynthesis || !text) return
    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.rate = 0.95
    utterance.pitch = 1.0
    utterance.volume = 1.0
    const voices = window.speechSynthesis.getVoices()
    const preferred = voices.find(
      (v) => v.name.includes('Google') || v.name.includes('Samantha') || v.lang === 'en-US'
    )
    if (preferred) utterance.voice = preferred
    utterance.onstart = () => setIsSpeaking(true)
    utterance.onend = () => setIsSpeaking(false)
    utterance.onerror = () => setIsSpeaking(false)
    window.speechSynthesis.speak(utterance)
  }, [])

  const stopSpeaking = useCallback(() => {
    window.speechSynthesis?.cancel()
    setIsSpeaking(false)
  }, [])

  return { isSpeaking, speak, stopSpeaking }
}
