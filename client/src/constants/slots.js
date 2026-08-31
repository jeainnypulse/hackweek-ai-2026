export const SLOT_LABELS = {
  regimen: 'Product',
  indication: 'Indication',
  book: 'Book',
  coverage: 'Coverage',
  organization: 'Payer',
  population: 'Subtype',
  line: 'Line of therapy',
}

export const SLOT_DISPLAY_ORDER = [
  'regimen',
  'indication',
  'book',
  'organization',
  'coverage',
  'population',
  'line',
]

export function emptyCascadeSelections() {
  return {
    coverage: '',
    population: '',
    line: '',
  }
}
