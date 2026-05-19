/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        navy: {
          950: '#030712',
          900: '#0a0f1e',
          800: '#0d1629',
          700: '#111d3a',
          600: '#162347',
        },
        accent: {
          DEFAULT: '#3b82f6',
          glow: '#60a5fa',
          dim: '#1d4ed8',
        },
        cyan: {
          glow: '#06b6d4',
        },
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'bounce-bar': 'bounceBar 1s ease-in-out infinite',
        'fade-in': 'fadeIn 0.3s ease-in',
        'slide-up': 'slideUp 0.3s ease-out',
        'glow-pulse': 'glowPulse 2s ease-in-out infinite',
      },
      keyframes: {
        bounceBar: {
          '0%, 100%': { transform: 'scaleY(0.4)' },
          '50%': { transform: 'scaleY(1)' },
        },
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        glowPulse: {
          '0%, 100%': { boxShadow: '0 0 20px rgba(59,130,246,0.4)' },
          '50%': { boxShadow: '0 0 40px rgba(59,130,246,0.8), 0 0 60px rgba(59,130,246,0.4)' },
        },
      },
      boxShadow: {
        'glow-blue': '0 0 20px rgba(59, 130, 246, 0.4)',
        'glow-blue-lg': '0 0 40px rgba(59, 130, 246, 0.6)',
        'glow-cyan': '0 0 20px rgba(6, 182, 212, 0.4)',
        'card': '0 4px 24px rgba(0, 0, 0, 0.4)',
      },
    },
  },
  plugins: [],
}
