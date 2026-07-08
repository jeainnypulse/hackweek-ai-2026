export function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/)
  if (lines.length < 2) return []

  const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, ''))
  const rows = []

  for (let i = 1; i < lines.length; i++) {
    const values = []
    let current = ''
    let inQuotes = false

    for (const char of lines[i]) {
      if (char === '"') {
        inQuotes = !inQuotes
        continue
      }
      if (char === ',' && !inQuotes) {
        values.push(current.trim())
        current = ''
        continue
      }
      current += char
    }
    values.push(current.trim())

    const row = {}
    headers.forEach((header, index) => {
      row[header] = (values[index] || '').replace(/^"|"$/g, '')
    })
    if (row.question) rows.push(row)
  }

  return rows
}

export function normalizeEvalRow(row, index, defaultMethod) {
  return {
    id: row.id || String(index + 1),
    type: row.type || '',
    question: row.question,
    expected_answer: row.expected_answer || row.answer || '',
    citation: row.citation || '',
    retrieval_method: row.retrieval_method || defaultMethod,
  }
}

export function normalizeAccessText(text) {
  return text
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .replace(/[;]/g, ' ')
    .replace(/\bwith\b/g, ' ')
    .replace(/\band\b/g, ' ')
    .trim()
}

export function accessParts(term) {
  return term
    .split(/[;,]|\s+with\s+|\s+and\s+/i)
    .map(part => part.trim().toLowerCase())
    .filter(part => part.length > 2)
}

export function extractComparisonTerms(expected) {
  const accessQuotes = [...expected.matchAll(/(?:is|has|have|are|was)\s+'([^']+)'/gi)]
    .map(match => match[1].trim())
  if (accessQuotes.length) return accessQuotes

  const quoted = [...expected.matchAll(/'([A-Z][^']*)'/g)].map(match => match[1].trim())
  if (quoted.length) return quoted

  return expected
    .split(/[,;]/)
    .map(term => term.trim())
    .filter(term => term.length > 2)
}

export function termMatchesActual(term, actualNorm) {
  const parts = accessParts(term)
  if (parts.length === 0) {
    return actualNorm.includes(normalizeAccessText(term))
  }
  return parts.every(part => actualNorm.includes(part))
}

export function compareAnswers(expected, actual) {
  const expectedNorm = normalizeAccessText(expected)
  const actualNorm = normalizeAccessText(actual)

  if (!expected.trim()) return { score: null, match: null, matched_terms: [] }

  const quoted = extractComparisonTerms(expected)
  if (quoted.length) {
    const matched = quoted.filter(term => termMatchesActual(term, actualNorm))
    const score = matched.length / quoted.length
    return {
      score: Math.round(score * 100) / 100,
      match: matched.length === quoted.length,
      matched_terms: matched,
    }
  }

  if (expectedNorm.length > 20 && actualNorm.includes(expectedNorm.slice(0, 40))) {
    return { score: 1, match: true, matched_terms: [expected.slice(0, 40)] }
  }

  const numbers = [...expected.matchAll(/\d+/g)].map(match => match[0])
  if (numbers.length) {
    const matched = numbers.filter(number => actual.includes(number))
    const score = matched.length / numbers.length
    return {
      score: Math.round(score * 100) / 100,
      match: score >= 0.5,
      matched_terms: matched,
    }
  }

  const terms = extractComparisonTerms(expected)
  if (terms.length) {
    const matched = terms.filter(term => termMatchesActual(term, actualNorm))
    const score = matched.length / terms.length
    return {
      score: Math.round(score * 100) / 100,
      match: score >= 0.5,
      matched_terms: matched,
    }
  }

  return { score: 0, match: false, matched_terms: [] }
}

export function resultsToCsv(results) {
  const header = ['id', 'type', 'question', 'expected_answer', 'citation', 'actual_answer', 'match', 'score', 'records_retrieved', 'error']
  const lines = [header.join(',')]
  for (const row of results) {
    const values = [
      row.id,
      row.type,
      row.question,
      row.expected_answer,
      row.citation,
      row.actual_answer,
      row.comparison?.match,
      row.comparison?.score,
      row.records_retrieved,
      row.error || '',
    ].map(value => `"${String(value ?? '').replace(/"/g, '""')}"`)
    lines.push(values.join(','))
  }
  return lines.join('\n')
}

export async function downloadJobResults(jobId) {
  const res = await fetch(`/api/eval/jobs/${jobId}/results.csv`)
  if (!res.ok) throw new Error('Failed to download results')
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `eval_results_${jobId.slice(0, 8)}.csv`
  link.click()
  URL.revokeObjectURL(url)
}
