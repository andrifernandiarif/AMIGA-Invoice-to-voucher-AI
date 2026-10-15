import { Feedback, LoadingState } from "../../components/ui/Feedback";
import { useClients } from "./useClients";

export function ClientManager() {
  const { clients, name, setName, code, setCode, message, busy, loading, submit } = useClients();
  const success = message === "Company created.";
  return (
    <section className="card feature-card client-card">
      <div className="feature-card-heading">
        <p className="feature-eyebrow">Organization</p>
        <h1>Companies</h1>
        <p className="feature-description">Create and review the companies available for assignment.</p>
      </div>
      <form className="management-form client-form" onSubmit={submit}>
        <label>Company name<input value={name} onChange={(event) => setName(event.target.value)} required /></label>
        <label>Code (optional)<input value={code} onChange={(event) => setCode(event.target.value.toUpperCase())} pattern="[A-Z0-9][A-Z0-9-]{0,39}" /></label>
        <button className="primary action-button" disabled={busy}>{busy ? "Creating…" : "Create company"}</button>
      </form>
      <Feedback message={message} tone={success ? "success" : "error"} />
      {loading ? <LoadingState label="Loading companies…" /> : <div className="client-list">{clients.map((client) => <div className="client-list-item" key={client.client_id}><strong>{client.client_name}</strong><span>{client.client_code || "No code"}</span></div>)}</div>}
    </section>
  );
}
