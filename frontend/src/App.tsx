import { useEffect, useState } from "react";

interface CdrResponse {
  status: string;
  items: unknown[];
  message: string;
}

export default function App() {
  const [data, setData] = useState<CdrResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/cdrs")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((json: CdrResponse) => setData(json))
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <main className="container">
      <h1>CDR Update Tracking</h1>
      <p>Track newly created Canadian Depositary Receipts and live market data.</p>

      <section className="card">
        <h2>Backend API Status</h2>
        {error && <p className="error">Error connecting to API: {error}</p>}
        {!error && !data && <p>Loading...</p>}
        {data && (
          <div>
            <p>
              Status: <strong>{data.status}</strong>
            </p>
            <p>{data.message}</p>
          </div>
        )}
      </section>
    </main>
  );
}

