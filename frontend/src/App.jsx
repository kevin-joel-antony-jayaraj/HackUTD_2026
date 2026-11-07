import { useEffect, useState } from 'react'
import './App.css'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

function App() {
  const [equipment, setEquipment] = useState([])

  useEffect(() => {
    fetch(`${API_BASE}/equipment/`)
      .then((res) => res.json())
      .then(setEquipment)
      .catch(console.error)
  }, [])

  return (
    <div>
      <h1>Predictive Maintenance Dashboard</h1>
      <ul>
        {equipment.map((item) => (
          <li key={item.id}>
            {item.name} — {item.equipment_type}
          </li>
        ))}
      </ul>
    </div>
  )
}

export default App
