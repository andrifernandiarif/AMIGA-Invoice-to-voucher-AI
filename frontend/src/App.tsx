import { BrowserRouter, Routes, Route, useParams } from 'react-router-dom';
import ReviewWorkspace from './components/ReviewWorkspace';

function ReviewWorkspaceRoute() {
  const { requestId } = useParams<{ requestId: string }>();
  
  if (!requestId) {
    return (
      <div className="error-page">
        <h1>Request ID Required</h1>
        <p>Please provide a valid request ID in the URL.</p>
      </div>
    );
  }

  return <ReviewWorkspace requestId={requestId} />;
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/requests/:requestId/review" element={<ReviewWorkspaceRoute />} />
        <Route path="/" element={
          <div className="home">
            <h1>AMIGA Robot Admin</h1>
            <p>Invoice-to-Voucher Processing Platform</p>
            <p>Navigate to a request to review its draft.</p>
            <p>Example: /requests/123e4567-e89b-12d3-a456-426614174000/review</p>
          </div>
        } />
      </Routes>
    </BrowserRouter>
  );
}

export default App;