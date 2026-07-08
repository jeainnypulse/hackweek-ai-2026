export function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/)
  if (lines.length < 2) return []

  const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, ''))
  const rows = []

  for (let lineIndex = 1; lineIndex < lines.length; lineIndex++) {
    const line = lines[lineIndex]
    const values = []
    let current = ''
    let inQuotes = false

    for (let i = 0; i < line.length; i++) {
      const char = line[i]
      if (char === '"') {
        if (inQuotes && line[i + 1] === '"') {
          current += '"'
          i += 1
          continue
        }
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

export function parseExpectedSlots(raw) {
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

export function normalizeEvalRow(row, index, defaultMethod) {
  const expectedAccess = row.expected_access || row.expected_answer || row.answer || ''
  return {
    id: row.id || String(index + 1),
    type: row.type || '',
    question: row.question,
    expected_access: expectedAccess,
    expected_slots: parseExpectedSlots(row.expected_slots),
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

function compareAccessLabel(expectedAccess, actualNorm) {
  const parts = accessParts(expectedAccess)
  if (!parts.length) {
    const normalized = normalizeAccessText(expectedAccess)
    const match = actualNorm.includes(normalized)
    return {
      score: match ? 1 : 0,
      match,
      matched_terms: match ? [expectedAccess] : [],
    }
  }

  const matched = parts.filter(part => actualNorm.includes(part))
  const score = matched.length / parts.length
  return {
    score: Math.round(score * 100) / 100,
    match: matched.length === parts.length,
    matched_terms: matched,
  }
}

const SLOT_FIELDS = [
  'regimen',
  'indication',
  'line',
  'book',
  'organization',
  'coverage',
  'population',
]

function normalizeSlotValue(value) {
  if (value == null) return null
  const text = String(value).trim()
  if (!text || text.toLowerCase() === 'null') return null
  return text.toLowerCase()
}

export function compareSlots(expectedSlots, actualSlots) {
  if (!expectedSlots) {
    return { score: null, match: null, matched_fields: [], mismatched_fields: [] }
  }

  const matchedFields = []
  const mismatchedFields = []
  const checkedFields = []

  for (const field of SLOT_FIELDS) {
    const expected = normalizeSlotValue(expectedSlots[field])
    if (expected == null) continue
    checkedFields.push(field)
    const actual = normalizeSlotValue(actualSlots?.[field])
    if (actual === expected) {
      matchedFields.push(field)
    } else {
      mismatchedFields.push({
        field,
        expected: expectedSlots[field],
        actual: actualSlots?.[field] ?? null,
      })
    }
  }

  if (!checkedFields.length) {
    return { score: null, match: null, matched_fields: [], mismatched_fields: [] }
  }

  const score = matchedFields.length / checkedFields.length
  return {
    score: Math.round(score * 100) / 100,
    match: mismatchedFields.length === 0,
    matched_fields: matchedFields,
    mismatched_fields: mismatchedFields,
  }
}

export function compareEvalResult(expectedAccess, expectedSlots, actualAnswer, actualSlots) {
  const access = compareAnswers(expectedAccess, actualAnswer)
  const slots = compareSlots(expectedSlots, actualSlots)

  const accessMatch = access.match
  const slotsMatch = slots.match

  let overallMatch = null
  if (accessMatch == null && slotsMatch == null) {
    overallMatch = null
  } else if (slotsMatch == null) {
    overallMatch = accessMatch
  } else if (accessMatch == null) {
    overallMatch = slotsMatch
  } else {
    overallMatch = accessMatch && slotsMatch
  }

  return {
    match: overallMatch,
    access,
    slots,
    actual_slots: actualSlots,
  }
}

export function compareAnswers(expectedAccess, actual) {
  const label = (expectedAccess || '').trim()
  const actualNorm = normalizeAccessText(actual)

  if (!label) return { score: null, match: null, matched_terms: [] }

  return compareAccessLabel(label, actualNorm)
}

export function resultsToCsv(results) {
  const header = [
    'id', 'type', 'question', 'expected_access', 'expected_slots', 'citation',
    'actual_answer', 'actual_slots', 'match', 'access_match', 'access_score',
    'slots_match', 'slots_score', 'records_retrieved', 'error',
  ]
  const lines = [header.join(',')]
  for (const row of results) {
    const comparison = row.comparison || {}
    const access = comparison.access || {}
    const slots = comparison.slots || {}
    const values = [
      row.id,
      row.type,
      row.question,
      row.expected_access,
      row.expected_slots ? JSON.stringify(row.expected_slots) : '',
      row.citation,
      row.actual_answer,
      comparison.actual_slots ? JSON.stringify(comparison.actual_slots) : '',
      comparison.match,
      access.match,
      access.score,
      slots.match,
      slots.score,
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
