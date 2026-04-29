export default function PaySuccess() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center p-8 gap-3">
      <div className="text-3xl font-semibold">Payment received</div>
      <p className="text-muted-foreground max-w-md text-center">
        Thanks — you'll get an SMS and email confirmation shortly. You can close
        this window.
      </p>
    </main>
  );
}
