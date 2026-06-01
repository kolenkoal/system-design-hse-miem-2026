import http from 'k6/http';
import { check, sleep } from 'k6';

export let options = {
  stages: [
    { duration: '30s', target: 1000 },
    { duration: '30s', target: 100 },
    { duration: '1m', target: 200 },
    { duration: '2m', target: 0 },
  ],
};

function randomInt(min, max){
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

export default function() {
  if (Math.random() < 0.8) {
    const payload = JSON.stringify({ user_id: randomInt(1,2), amount: Math.random()*100, description: 'k6 load' });
    const res = http.post('http://localhost:8081/api/orders', payload, { headers: { 'Content-Type': 'application/json' } });
    check(res, { 'created': (r) => r.status === 200 || r.status === 201 });
  } else {
    const res = http.get('http://localhost:8080/api/orders');
    check(res, { 'list ok': (r) => r.status === 200 });
  }
  sleep(0.05);
}

