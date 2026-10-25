/**
 * Normalizes technical markdown, Windows file paths, and punctuation
 * into natural, human-like speech for Text-To-Speech (TTS) engines.
 */
export function cleanTextForSpeech(text: string): string {
  if (!text) return ''

  let s = text

  // 1. Code blocks (```...```) -> conversational placeholder
  s = s.replace(/```[\s\S]*?```/g, ' Here is the requested code. ')

  // 2. Raw JSON objects -> natural statement
  const trimmed = s.trim()
  if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
    try {
      const data = JSON.parse(trimmed)
      if (typeof data === 'object' && data !== null) {
        if (data.message) {
          s = String(data.message)
        } else if (data.name) {
          s = `Running ${String(data.name).replace(/_/g, ' ')}.`
        } else {
          s = 'I have processed the request.'
        }
      }
    } catch {
      s = s.replace(/[{}\[\]"]/g, ' ')
    }
  }

  // 3. Markdown links: [Title](url) -> Title
  s = s.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')

  // 4. Raw URLs
  s = s.replace(/https?:\/\/\S+/g, '')

  // 5. HTML tags
  s = s.replace(/<[^>]+>/g, ' ')

  // 6. Plural indicators: e.g. file(s) -> files, application(s) -> applications
  s = s.replace(/(\w+)\(s\)/gi, '$1s')
  s = s.replace(/(\w+)\(es\)/gi, '$1es')

  // 7. Redundant bullet paths: e.g. "- **name** (size) — `C:\path`" -> "- **name** (size)"
  s = s.replace(/—\s*[`'"]?[A-Za-z]:\\[^`'\n]+[`'"]?/g, '')

  // 8. Windows paths in parentheses: e.g. (`C:\Windows\System32\notepad.exe`) -> ""
  s = s.replace(/\([`'"]?[A-Za-z]:\\[^)]+[`'"]?\)/g, '')

  // 9. Standalone Windows paths: C:\Users\vaibh\Documents -> Documents folder
  s = s.replace(/[A-Za-z]:\\[\w\s.\-\\]+/g, (match) => {
    const raw = match.replace(/[`'"]/g, '')
    const parts = raw.replace(/\//g, '\\').split('\\').filter(Boolean)
    return parts.length > 1 ? ` ${parts[parts.length - 1]} ` : parts[0] || ''
  })

  // 10. ELIMINATE ALL BACKSLASHES AND SLASHES - A human never says "backslash"!
  s = s.replace(/\\+/g, ' ')
  s = s.replace(/(?<=\w)\/(?=\w)/g, ' and ')
  s = s.replace(/\/+/g, ' ')

  // 11. Markdown headers
  s = s.replace(/^\s*#{1,6}\s*(.+)$/gm, '$1.')

  // 12. File sizes: (0.4 KB) -> 0.4 kilobytes
  s = s.replace(/\(\s*(\d+(?:\.\d+)?)\s*KB\s*\)/gi, ', $1 kilobytes')
  s = s.replace(/\(\s*(\d+(?:\.\d+)?)\s*MB\s*\)/gi, ', $1 megabytes')
  s = s.replace(/\(\s*(\d+(?:\.\d+)?)\s*GB\s*\)/gi, ', $1 gigabytes')

  // 13. List bullets: replace with pauses
  s = s.replace(/^\s*[-*•]\s+/gm, ', ')
  s = s.replace(/^\s*\d+\.\s+/gm, ', ')

  // 14. Technical underscores in names: search_installed_apps -> search installed apps
  s = s.replace(/(?<=\w)_(?=\w)/g, ' ')

  // 15. Markdown symbols (bold, backticks, pipes, tildes)
  s = s.replace(/[*_~`>|#]/g, ' ')

  // 16. Normalize curly quotes and apostrophes to standard ASCII
  s = s.replace(/[’‘]/g, "'")
  s = s.replace(/[“”]/g, '"')

  // 17. Technical brackets, symbols, and double quotes
  s = s.replace(/[\[\]{}()^@$%&+=~"]/g, ' ')
  s = s.replace(/—|–|--/g, ', ')

  // 18. Standalone single quotes (strip quotes while preserving contraction apostrophes like don't, can't, it's, you're, I'm, won't)
  s = s.replace(/(?<![A-Za-z0-9])'|'(?![A-Za-z0-9])/g, ' ')

  // 19. Semicolon and Colon to natural pause commas
  s = s.replace(/;\s*/g, ', ')
  s = s.replace(/:\s+/g, ', ')

  // 20. Technical acronyms and terms
  s = s.replace(/\bAPI\b/g, 'A P I')
  s = s.replace(/\bAPIs\b/g, 'A P Is')
  s = s.replace(/\bGPU\b/g, 'G P U')
  s = s.replace(/\bGPUs\b/g, 'G P Us')
  s = s.replace(/\bPostgreSQL\b/gi, 'Post-gres Q L')
  s = s.replace(/\bPostgres\b/gi, 'Post-gres')
  s = s.replace(/\bSQL\b/g, 'sequel')
  s = s.replace(/\bUI\b/g, 'U I')
  s = s.replace(/\bGUI\b/g, 'gooey')
  s = s.replace(/\bCLI\b/g, 'C L I')
  s = s.replace(/\bCPU\b/g, 'C P U')
  s = s.replace(/\bFastAPI\b/g, 'Fast A P I')
  s = s.replace(/\bQdrant\b/g, 'Quadrant')

  // 21. Hinglish postposition "ko" (distinguish from K.O. / boxing knockout)
  if (/\b(?:karo|dekho|bhejo|samjho|rakho|do|de|batao|karna|hoga|hai|tha|thi|the|mera|meri|mere|aap|tum|hum|is|us|sab)\b/i.test(s)) {
    s = s.replace(/\bko\b/gi, 'koh')
  }
  s = s.replace(/\bK\.?O\.?\b/gi, 'K-O')

  // 22. Ellipses
  s = s.replace(/\.{2,}/g, '. ')

  // 23. Clean up spacing and commas
  s = s.replace(/\s+([,.:;?!])/g, '$1')
  s = s.replace(/,\s*,+/g, ',')
  s = s.replace(/\s+/g, ' ')

  return s.trim()
}
