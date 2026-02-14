"""
Instance Manager for Minecraft Server Scanner
Handles master/worker detection and inter-process communication
"""

import socket
import json
import threading
import time
import os
import sys
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass, asdict

# IPC Configuration
IPC_HOST = "127.0.0.1"
IPC_PORT = 9999
IPC_BUFFER_SIZE = 4096

@dataclass
class StatsMessage:
    """Message format for stats exchange between instances"""
    instance_id: str
    scanned: int
    found: int
    with_players: int
    sent_count: int
    is_disconnect: bool = False
    
    def to_json(self) -> str:
        return json.dumps(asdict(self))
    
    @classmethod
    def from_json(cls, data: str) -> "StatsMessage":
        return cls(**json.loads(data))


class InstanceManager:
    """Manages instance detection and IPC communication"""
    
    def __init__(self):
        self.is_master = False
        self.instance_id = f"{os.getpid()}_{int(time.time() * 1000)}"
        self.master_socket: Optional[socket.socket] = None
        self.server_socket: Optional[socket.socket] = None
        self.worker_sockets: Dict[str, socket.socket] = {}
        self.worker_stats: Dict[str, StatsMessage] = {}
        self.running = False
        self.lock = threading.Lock()
        self.stats_callback: Optional[Callable[[StatsMessage], None]] = None
        self.disconnect_callback: Optional[Callable[[str], None]] = None
        
    def check_master(self) -> bool:
        """
        Check if a master instance is already running.
        Returns True if this instance should be master, False if worker.
        """
        try:
            # Try to connect to existing master
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_socket.settimeout(1)
            test_socket.connect((IPC_HOST, IPC_PORT))
            test_socket.close()
            # Connection successful = master exists
            self.is_master = False
            return False
        except (socket.error, ConnectionRefusedError):
            # No master found, this will be the master
            self.is_master = True
            return True
    
    def start_as_master(self, stats_callback: Optional[Callable[[StatsMessage], None]] = None,
                       disconnect_callback: Optional[Callable[[str], None]] = None):
        """Start as master instance - runs IPC server"""
        self.stats_callback = stats_callback
        self.disconnect_callback = disconnect_callback
        self.running = True
        
        # Create server socket
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((IPC_HOST, IPC_PORT))
        self.server_socket.listen(5)
        
        # Start server thread
        server_thread = threading.Thread(target=self._server_loop, daemon=True)
        server_thread.start()
        
        print(f"[INSTANCE] Started as MASTER (ID: {self.instance_id})")
        return True
    
    def start_as_worker(self) -> bool:
        """Start as worker instance - connect to master"""
        try:
            self.master_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.master_socket.connect((IPC_HOST, IPC_PORT))
            self.running = True
            
            # Start heartbeat thread
            heartbeat_thread = threading.Thread(target=self._worker_heartbeat, daemon=True)
            heartbeat_thread.start()
            
            print(f"[INSTANCE] Started as WORKER (ID: {self.instance_id})")
            return True
        except Exception as e:
            print(f"[INSTANCE] Failed to connect as worker: {e}")
            return False
    
    def _server_loop(self):
        """Server loop for master - accepts worker connections"""
        self.server_socket.settimeout(1.0)  # Allow checking self.running periodically
        
        while self.running:
            try:
                client_socket, address = self.server_socket.accept()
                client_thread = threading.Thread(
                    target=self._handle_worker,
                    args=(client_socket,),
                    daemon=True
                )
                client_thread.start()
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"[MASTER] Server error: {e}")
    
    def _handle_worker(self, client_socket: socket.socket):
        """Handle communication with a single worker"""
        client_socket.settimeout(5.0)
        worker_id = None
        
        try:
            while self.running:
                try:
                    data = client_socket.recv(IPC_BUFFER_SIZE)
                    if not data:
                        break
                    
                    message = StatsMessage.from_json(data.decode('utf-8'))
                    worker_id = message.instance_id
                    
                    with self.lock:
                        if message.is_disconnect:
                            if worker_id in self.worker_sockets:
                                del self.worker_sockets[worker_id]
                            if worker_id in self.worker_stats:
                                del self.worker_stats[worker_id]
                            if self.disconnect_callback:
                                self.disconnect_callback(worker_id)
                            print(f"[MASTER] Worker {worker_id[:8]}... disconnected")
                            break
                        else:
                            self.worker_stats[worker_id] = message
                            self.worker_sockets[worker_id] = client_socket
                            if self.stats_callback:
                                self.stats_callback(message)
                    
                    # Send acknowledgment
                    ack = json.dumps({"status": "ok"})
                    client_socket.send(ack.encode('utf-8'))
                    
                except socket.timeout:
                    continue
                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    print(f"[MASTER] Worker handler error: {e}")
                    break
        except Exception as e:
            print(f"[MASTER] Worker connection error: {e}")
        finally:
            # Cleanup on disconnect
            if worker_id:
                with self.lock:
                    if worker_id in self.worker_sockets:
                        del self.worker_sockets[worker_id]
                    if worker_id in self.worker_stats:
                        del self.worker_stats[worker_id]
                    if self.disconnect_callback:
                        self.disconnect_callback(worker_id)
            try:
                client_socket.close()
            except:
                pass
    
    def _worker_heartbeat(self):
        """Worker thread - sends periodic stats to master"""
        while self.running:
            try:
                # Stats will be updated externally and sent here
                time.sleep(2)  # Heartbeat interval
            except:
                break
    
    def send_worker_stats(self, scanned: int, found: int, with_players: int, sent_count: int):
        """Send stats update from worker to master"""
        if not self.master_socket or not self.running:
            return
        
        try:
            message = StatsMessage(
                instance_id=self.instance_id,
                scanned=scanned,
                found=found,
                with_players=with_players,
                sent_count=sent_count
            )
            self.master_socket.send(message.to_json().encode('utf-8'))
            
            # Receive acknowledgment
            self.master_socket.settimeout(2.0)
            try:
                ack = self.master_socket.recv(IPC_BUFFER_SIZE)
            except socket.timeout:
                pass
        except Exception as e:
            print(f"[WORKER] Failed to send stats: {e}")
    
    def disconnect_worker(self):
        """Send disconnect message and close worker connection"""
        if self.master_socket and self.running:
            try:
                message = StatsMessage(
                    instance_id=self.instance_id,
                    scanned=0, found=0, with_players=0, sent_count=0,
                    is_disconnect=True
                )
                self.master_socket.send(message.to_json().encode('utf-8'))
                time.sleep(0.1)  # Give time for message to be sent
            except:
                pass
            finally:
                try:
                    self.master_socket.close()
                except:
                    pass
    
    def get_all_stats(self) -> Dict[str, Any]:
        """Get aggregated stats from all workers (master only)"""
        with self.lock:
            total_scanned = 0
            total_found = 0
            total_with_players = 0
            total_sent = 0
            active_workers = len(self.worker_stats)
            
            for stats in self.worker_stats.values():
                total_scanned += stats.scanned
                total_found += stats.found
                total_with_players += stats.with_players
                total_sent += stats.sent_count
            
            return {
                "active_workers": active_workers,
                "total_scanned": total_scanned,
                "total_found": total_found,
                "total_with_players": total_with_players,
                "total_sent": total_sent,
                "worker_details": {
                    wid: {
                        "scanned": s.scanned,
                        "found": s.found,
                        "with_players": s.with_players,
                        "sent_count": s.sent_count
                    }
                    for wid, s in self.worker_stats.items()
                }
            }
    
    def stop(self):
        """Stop the instance manager"""
        self.running = False
        
        if self.is_master:
            # Close all worker connections
            with self.lock:
                for sock in self.worker_sockets.values():
                    try:
                        sock.close()
                    except:
                        pass
                self.worker_sockets.clear()
            
            if self.server_socket:
                try:
                    self.server_socket.close()
                except:
                    pass
        else:
            self.disconnect_worker()


# Singleton instance
_instance_manager: Optional[InstanceManager] = None

def get_instance_manager() -> InstanceManager:
    """Get or create the singleton instance manager"""
    global _instance_manager
    if _instance_manager is None:
        _instance_manager = InstanceManager()
    return _instance_manager
