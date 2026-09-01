import { Colors, Typography } from "@pulse-analytics/pulse-design-system";

export default function AppHeader() {
  return (
    <Typography variant="Body50.Regular" style={{ color: Colors.Gray500 }}>
      AI answers to your payer-access questions, pulled live from your Quality
      of Access data.
      <br />
      <div style={{ margin: "24px 0" }}>Try asking:</div>
    </Typography>
  );
}
