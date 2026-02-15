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
    # Advanced stats
    peak_scans_per_minute: float = 0.0
    peak_found_per_minute: float = 0.0
    scans_per_minute: float = 0.0
    found_per_minute: float = 0.0
    
    def to_json(self) -> str:
        return json.dumps(asdict(self))
    
    @classmethod
    def from_json(cls, data: str) -> "StatsMessage":
        return cls(**json.loads(data))



@dataclass
class ServerCheckMessage:
    """Message format for checking if a server was already sent"""
    instance_id: str
    server_key: str  # "ip:port"
    message_type: str = "check_server"  # "check_server" or "mark_server"
    
    def to_json(self) -> str:
        return json.dumps(asdict(self))
    
    @classmethod
    def from_json(cls, data: str) -> "ServerCheckMessage":
        return cls(**json.loads(data))


@dataclass
class ServerResponseMessage:
    """Response message for server check"""
    server_key: str
    already_sent: bool
    broadcast: bool = False  # True if this is a broadcast to all workers
    
    def to_json(self) -> str:
        return json.dumps(asdict(self))
    
    @classmethod
    def from_json(cls, data: str) -> "ServerResponseMessage":
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
        
        # Server deduplication tracking (master only)
        self.sent_servers: set = set()  # Set of "ip:port" strings
        self.sent_servers_lock = threading.Lock()
        
        # Worker callback for server broadcasts
        self.server_broadcast_callback: Optional[Callable[[str], None]] = None

        
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
                       disconnect_callback: Optional[Callable[[str], None]] = None,
                       server_broadcast_callback: Optional[Callable[[str], None]] = None):
        """Start as master instance - runs IPC server"""
        self.stats_callback = stats_callback
        self.disconnect_callback = disconnect_callback
        self.server_broadcast_callback = server_broadcast_callback
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
                    
                    decoded_data = data.decode('utf-8')
                    
                    # Try to parse as StatsMessage first
                    try:
                        message = StatsMessage.from_json(decoded_data)
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
                        continue
                        
                    except (json.JSONDecodeError, TypeError, KeyError):
                        pass
                    
                    # Try to parse as ServerCheckMessage
                    try:
                        server_msg = ServerCheckMessage.from_json(decoded_data)
                        worker_id = server_msg.instance_id
                        
                        with self.sent_servers_lock:
                            already_sent = server_msg.server_key in self.sent_servers
                            
                            if server_msg.message_type == "mark_server" and not already_sent:
                                self.sent_servers.add(server_msg.server_key)
                                # Broadcast to all other workers
                                self._broadcast_server_to_workers(server_msg.server_key, exclude_worker=worker_id)
                        
                        # Send response
                        response = ServerResponseMessage(
                            server_key=server_msg.server_key,
                            already_sent=already_sent
                        )
                        client_socket.send(response.to_json().encode('utf-8'))
                        continue
                        
                    except (json.JSONDecodeError, TypeError, KeyError):
                        pass
                    
                except socket.timeout:
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
    
    def _broadcast_server_to_workers(self, server_key: str, exclude_worker: Optional[str] = None):
        """Broadcast a newly sent server to all workers except the sender"""
        broadcast_msg = ServerResponseMessage(
            server_key=server_key,
            already_sent=True,
            broadcast=True
        )
        message_data = broadcast_msg.to_json().encode('utf-8')
        
        with self.lock:
            for worker_id, worker_socket in self.worker_sockets.items():
                if worker_id != exclude_worker:
                    try:
                        worker_socket.send(message_data)
                    except Exception as e:
                        print(f"[MASTER] Failed to broadcast to worker {worker_id[:8]}: {e}")

    
    def _worker_heartbeat(self):
        """Worker thread - sends periodic stats to master"""
        while self.running:
            try:
                # Stats will be updated externally and sent here
                time.sleep(2)  # Heartbeat interval
            except:
                break
    
    def send_worker_stats(self, scanned: int, found: int, with_players: int, sent_count: int,
                          peak_scans_per_minute: float = 0.0, peak_found_per_minute: float = 0.0,
                          scans_per_minute: float = 0.0, found_per_minute: float = 0.0):
        """Send stats update from worker to master"""
        if not self.master_socket or not self.running:
            return
        
        try:
            message = StatsMessage(
                instance_id=self.instance_id,
                scanned=scanned,
                found=found,
                with_players=with_players,
                sent_count=sent_count,
                peak_scans_per_minute=peak_scans_per_minute,
                peak_found_per_minute=peak_found_per_minute,
                scans_per_minute=scans_per_minute,
                found_per_minute=found_per_minute
            )
            self.master_socket.send(message.to_json().encode('utf-8'))
            
            # Receive acknowledgment (non-blocking check for server broadcasts)
            self.master_socket.settimeout(0.1)
            try:
                data = self.master_socket.recv(IPC_BUFFER_SIZE)
                # Check if it's a broadcast message
                try:
                    msg = json.loads(data.decode('utf-8'))
                    if msg.get("broadcast") and self.server_broadcast_callback:
                        self.server_broadcast_callback(msg.get("server_key"))
                except:
                    pass
            except socket.timeout:
                pass
        except Exception as e:
            print(f"[WORKER] Failed to send stats: {e}")

    
    def check_server_sent(self, server_key: str) -> bool:
        """Check if a server was already sent (worker only)"""
        if not self.master_socket or not self.running or self.is_master:
            return False
        
        try:
            message = ServerCheckMessage(
                instance_id=self.instance_id,
                server_key=server_key,
                message_type="check_server"
            )
            self.master_socket.send(message.to_json().encode('utf-8'))
            
            # Wait for response
            self.master_socket.settimeout(5.0)
            data = self.master_socket.recv(IPC_BUFFER_SIZE)
            response = ServerResponseMessage.from_json(data.decode('utf-8'))
            return response.already_sent
            
        except Exception as e:
            print(f"[WORKER] Failed to check server status: {e}")
            return False
    
    def mark_server_sent(self, server_key: str) -> bool:
        """Mark a server as sent and notify master (worker only)"""
        if not self.master_socket or not self.running or self.is_master:
            return False
        
        try:
            message = ServerCheckMessage(
                instance_id=self.instance_id,
                server_key=server_key,
                message_type="mark_server"
            )
            self.master_socket.send(message.to_json().encode('utf-8'))
            
            # Wait for response
            self.master_socket.settimeout(5.0)
            data = self.master_socket.recv(IPC_BUFFER_SIZE)
            response = ServerResponseMessage.from_json(data.decode('utf-8'))
            return not response.already_sent  # Returns True if newly marked
            
        except Exception as e:
            print(f"[WORKER] Failed to mark server as sent: {e}")
            return False
    
    def set_server_broadcast_callback(self, callback: Callable[[str], None]):
        """Set callback for receiving server broadcasts from master"""
        self.server_broadcast_callback = callback

    
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
            
            # Advanced stats aggregation
            max_peak_scans = 0.0
            max_peak_found = 0.0
            total_scans_per_min = 0.0
            total_found_per_min = 0.0
            
            for stats in self.worker_stats.values():
                total_scanned += stats.scanned
                total_found += stats.found
                total_with_players += stats.with_players
                total_sent += stats.sent_count
                
                # Aggregate advanced stats
                max_peak_scans = max(max_peak_scans, stats.peak_scans_per_minute)
                max_peak_found = max(max_peak_found, stats.peak_found_per_minute)
                total_scans_per_min += stats.scans_per_minute
                total_found_per_min += stats.found_per_minute
            
            return {
                "active_workers": active_workers,
                "total_scanned": total_scanned,
                "total_found": total_found,
                "total_with_players": total_with_players,
                "total_sent": total_sent,
                # Advanced stats
                "max_peak_scans_per_minute": max_peak_scans,
                "max_peak_found_per_minute": max_peak_found,
                "total_scans_per_minute": total_scans_per_min,
                "total_found_per_minute": total_found_per_min,
                "worker_details": {
                    wid: {
                        "scanned": s.scanned,
                        "found": s.found,
                        "with_players": s.with_players,
                        "sent_count": s.sent_count,
                        "peak_scans_per_minute": s.peak_scans_per_minute,
                        "peak_found_per_minute": s.peak_found_per_minute,
                        "scans_per_minute": s.scans_per_minute,
                        "found_per_minute": s.found_per_minute
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
