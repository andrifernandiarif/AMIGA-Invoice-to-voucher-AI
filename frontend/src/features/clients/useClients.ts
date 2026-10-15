import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, Client, json } from "../../api";
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
  return { clients, name, setName, code, setCode, message, busy, loading, submit };
}
