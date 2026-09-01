import { Colors, Typography } from "@pulse-analytics/pulse-design-system";

export default function AppHeader() {
  return (
    <div className="pulse-intro">
      <Typography
        variant="Body50.Regular"
        className="pulse-intro-copy"
        style={{ color: Colors.Gray500 }}
      >
        AI answers to your payer-access questions, pulled live from your Quality
        of Access data.
      </Typography>
      <Typography
        variant="Body50.Regular"
        className="pulse-try-label"
        style={{ color: Colors.Gray500 }}
      >
        Try asking:
      </Typography>
    </div>
  );
}
