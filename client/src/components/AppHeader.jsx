export default function AppHeader() {
  return (
    <div className="mb-10">
      <h1 className="text-xl font-semibold text-gray-900">Pulse AI Chatbot</h1>
      <p className="text-sm text-gray-500 mt-2">
        Operating with the data permissions of{' '}
        <span className="font-medium text-gray-700">orca-payer-demo</span>
        {' '}— answers are limited to payers and products available to that
        account.
      </p>
    </div>
  )
}
