import ConfirmedSlots from './ConfirmedSlots'

function CascadeSelect({
  label,
  value,
  options,
  placeholder,
  disabled,
  onChange,
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">
        {label}
      </span>
      <select
        value={value}
        onChange={event => onChange(event.target.value)}
        disabled={disabled}
        className="mt-1 w-full text-sm border border-gray-200 rounded-lg px-3 py-2 outline-none bg-white focus:border-violet-300 disabled:opacity-50"
      >
        <option value="">{placeholder}</option>
        {options.map(option => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  )
}

export default function CascadeForm({
  result,
  selections,
  loading,
  ready,
  onChange,
  onSubmit,
}) {
  const pending = result.cascade?.pending_slots || []
  const options = result.cascade?.options || {}
  const needsPopulation = pending.includes('population')

  return (
    <>
      <p className="text-sm font-medium text-gray-900 mb-2">
        Here's what I have confirmed
      </p>
      <ConfirmedSlots slots={result.confirmed_slots} asList />
      <p className="text-sm text-gray-600 mb-4 pt-3 border-t border-gray-100">
        I would need some additional information from you to give you an
        accurate response
      </p>

      <div className="space-y-3">
        {pending.includes('coverage') && (
          <CascadeSelect
            label="Coverage"
            value={selections.coverage}
            options={options.coverage || []}
            placeholder="Select coverage…"
            disabled={loading}
            onChange={value => onChange('coverage', value)}
          />
        )}

        {needsPopulation && (
          <CascadeSelect
            label="Subtype"
            value={selections.population}
            options={options.population || []}
            placeholder={
              selections.coverage
                ? 'Select subtype…'
                : 'Select coverage first…'
            }
            disabled={loading || !selections.coverage}
            onChange={value => onChange('population', value)}
          />
        )}

        {pending.includes('line') && (
          <CascadeSelect
            label="Line"
            value={selections.line}
            options={options.line || []}
            placeholder={
              !selections.coverage
                ? 'Select coverage first…'
                : needsPopulation && !selections.population
                  ? 'Select subtype first…'
                  : 'Select line of therapy…'
            }
            disabled={
              loading
              || !selections.coverage
              || (needsPopulation && !selections.population)
            }
            onChange={value => onChange('line', value)}
          />
        )}
      </div>

      <div className="mt-4 flex items-center gap-2">
        <button
          onClick={onSubmit}
          disabled={loading || !ready}
          className="text-sm bg-violet-600 hover:bg-violet-700 disabled:opacity-40 disabled:cursor-not-allowed text-white px-4 py-1.5 rounded-lg transition-colors"
        >
          {loading ? 'Looking up…' : 'Get answer'}
        </button>
      </div>
    </>
  )
}
