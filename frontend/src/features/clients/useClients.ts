import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, apiFetch, Client, json } from "../../api";
import { errorMessage } from "../../utils/errors";

export function useClients() {
  const [clients, setClients] = useState<Client[]>([]);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setClients(await api<Client[]>("/clients/all"));
      setMessage("");
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      await api("/clients", json({ client_name: name, client_code: code || null }));
      setName("");
      setCode("");
      await load();
      setMessage("Company created.");
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function remove(client: Client) {
    if (!window.confirm(`Remove company "${client.client_name}"? It will no longer be available for new requests or assignments. Existing vouchers and audit history will be preserved.`)) return;
    setBusy(true);
    setMessage("");
    try {
      await apiFetch(`/api/clients/${client.client_id}`, { method: "DELETE" });
      await load();
      setMessage("Company removed.");
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return { remove, clients, name, setName, code, setCode, message, busy, loading, submit };
}
