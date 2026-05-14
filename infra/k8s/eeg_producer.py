"""
EEG Data Kafka Producer

Đọc dữ liệu sóng não từ file .edf, chia thành chunks 1 giây,
và gửi vào Kafka topic với patient_id làm message key.
"""

import json
import logging
import os
from typing import Dict, List, Tuple
from datetime import datetime

import mne
import numpy as np
from kafka import KafkaProducer
from kafka.errors import KafkaError

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class EEGKafkaProducer:
    """Producer cho EEG data từ file .edf vào Kafka"""
    
    def __init__(self, bootstrap_servers: str, topic: str):
        """
        Khởi tạo Kafka Producer
        
        Args:
            bootstrap_servers: Kafka bootstrap servers (e.g., 'localhost:9092,localhost:9094,localhost:9096')
            topic: Kafka topic name
        """
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        
        # Khởi tạo Kafka Producer với acks='all' để đảm bảo an toàn dữ liệu
        self.producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers.split(','),
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            acks='all',  # Chờ xác nhận từ tất cả replicas
            retries=3,
            max_in_flight_requests_per_connection=1,  # Đảm bảo thứ tự
            request_timeout_ms=30000,
        )
        logger.info(f"Kafka Producer initialized: {bootstrap_servers}")
    
    def read_edf_file(self, edf_path: str) -> Tuple[mne.io.BaseRaw, str]:
        """
        Đọc file EDF
        
        Args:
            edf_path: Đường dẫn tới file .edf
            
        Returns:
            (mne.io.BaseRaw object, patient_id)
        """
        try:
            raw = mne.io.read_raw_edf(edf_path, preload=True)
            logger.info(f"Successfully read EDF file: {edf_path}")
            
            # Trích xuất patient_id từ tên file (e.g., S001R01.edf -> S001)
            filename = os.path.basename(edf_path)
            patient_id = filename.split('R')[0] if 'R' in filename else filename.replace('.edf', '')
            
            return raw, patient_id
        except Exception as e:
            logger.error(f"Error reading EDF file {edf_path}: {e}")
            raise
    
    def extract_eeg_info(self, raw: mne.io.BaseRaw, patient_id: str) -> Dict:
        """
        Trích xuất thông tin từ EDF file
        
        Args:
            raw: mne.io.BaseRaw object
            patient_id: Patient ID
            
        Returns:
            Dictionary với thông tin EEG
        """
        info = {
            'patient_id': patient_id,
            'sampling_rate': raw.info['sfreq'],  # Sampling rate (Hz)
            'n_channels': len(raw.ch_names),
            'channel_names': raw.ch_names,
            'duration_seconds': raw.times[-1],  # Tổng thời gian ghi (giây)
            'n_samples': raw.n_times,
        }
        
        logger.info(f"Patient {patient_id}: {info['n_channels']} channels, "
                   f"Sampling rate: {info['sampling_rate']} Hz, "
                   f"Duration: {info['duration_seconds']:.2f} seconds")
        
        return info
    
    def chunk_eeg_data(self, raw: mne.io.BaseRaw, patient_id: str, 
                       chunk_duration_sec: int = 1) -> List[Dict]:
        """
        Chia dữ liệu EEG thành các chunks theo thời gian
        
        Args:
            raw: mne.io.BaseRaw object
            patient_id: Patient ID
            chunk_duration_sec: Thời lượng mỗi chunk (giây)
            
        Returns:
            List of chunks (dictionaries)
        """
        sampling_rate = raw.info['sfreq']
        chunk_samples = int(sampling_rate * chunk_duration_sec)
        
        # Lấy dữ liệu từ tất cả channels
        data = raw.get_data()  # Shape: (n_channels, n_samples)
        n_samples = data.shape[1]
        n_channels = data.shape[0]
        
        chunks = []
        chunk_id = 0
        
        for start_idx in range(0, n_samples, chunk_samples):
            end_idx = min(start_idx + chunk_samples, n_samples)
            
            # Trích xuất dữ liệu chunk
            chunk_data = data[:, start_idx:end_idx]
            
            # Chỉ gửi chunks đủ dữ liệu (hoặc các chunk cuối nếu muốn)
            if chunk_data.shape[1] > 0:
                start_time = start_idx / sampling_rate
                end_time = end_idx / sampling_rate
                
                chunk_dict = {
                    'patient_id': patient_id,
                    'chunk_id': chunk_id,
                    'start_time_sec': float(start_time),
                    'end_time_sec': float(end_time),
                    'sampling_rate': sampling_rate,
                    'n_channels': n_channels,
                    'channel_names': raw.ch_names,
                    'data': chunk_data.tolist(),  # Chuyển numpy array thành list
                    'timestamp': datetime.utcnow().isoformat(),
                }
                
                chunks.append(chunk_dict)
                chunk_id += 1
        
        logger.info(f"Divided data into {len(chunks)} chunks of {chunk_duration_sec} second(s)")
        return chunks
    
    def send_chunk_to_kafka(self, chunk: Dict) -> bool:
        """
        Gửi một chunk EEG data vào Kafka
        
        Args:
            chunk: Dictionary chứa chunk data
            
        Returns:
            True nếu gửi thành công, False nếu thất bại
        """
        try:
            patient_id = chunk['patient_id']
            chunk_id = chunk['chunk_id']
            
            # Sử dụng patient_id làm message key để đảm bảo thứ tự
            message_key = f"{patient_id}".encode('utf-8')
            
            # Gửi message
            future = self.producer.send(
                self.topic,
                value=chunk,
                key=message_key
            )
            
            # Chờ xác nhận
            record_metadata = future.get(timeout=30)
            
            logger.info(f"Sent chunk {chunk_id} for patient {patient_id}: "
                       f"partition={record_metadata.partition}, "
                       f"offset={record_metadata.offset}")
            
            return True
            
        except KafkaError as e:
            logger.error(f"Failed to send chunk {chunk.get('chunk_id', 'unknown')} "
                        f"for patient {chunk.get('patient_id', 'unknown')}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending chunk: {e}")
            return False
    
    def process_and_send_edf_file(self, edf_path: str, chunk_duration_sec: int = 1) -> Tuple[int, int]:
        """
        Đọc file EDF, chia thành chunks, và gửi vào Kafka
        
        Args:
            edf_path: Đường dẫn tới file .edf
            chunk_duration_sec: Thời lượng mỗi chunk (giây)
            
        Returns:
            (total_chunks, successful_chunks)
        """
        try:
            # 1. Đọc file EDF
            logger.info(f"Reading EDF file: {edf_path}")
            raw, patient_id = self.read_edf_file(edf_path)
            
            # 2. Trích xuất thông tin
            eeg_info = self.extract_eeg_info(raw, patient_id)
            
            # 3. Chia thành chunks
            logger.info(f"Creating chunks of {chunk_duration_sec} second(s)...")
            chunks = self.chunk_eeg_data(raw, patient_id, chunk_duration_sec)
            
            # 4. Gửi chunks vào Kafka
            logger.info(f"Sending {len(chunks)} chunks to Kafka topic '{self.topic}'...")
            
            successful = 0
            for chunk in chunks:
                if self.send_chunk_to_kafka(chunk):
                    successful += 1
            
            # Đợi producer hoàn thành
            self.producer.flush(timeout=30)
            
            logger.info(f"\n{'='*60}")
            logger.info(f"Processing complete!")
            logger.info(f"Total chunks: {len(chunks)}")
            logger.info(f"Successfully sent: {successful}/{len(chunks)}")
            logger.info(f"Patient ID: {patient_id}")
            logger.info(f"Sampling rate: {eeg_info['sampling_rate']} Hz")
            logger.info(f"Channels: {eeg_info['n_channels']}")
            logger.info(f"{'='*60}\n")
            
            return len(chunks), successful
            
        except Exception as e:
            logger.error(f"Error processing EDF file: {e}")
            raise
    
    def close(self):
        """Đóng Kafka Producer connection"""
        self.producer.close()
        logger.info("Kafka Producer closed")


def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='EEG Kafka Producer')
    parser.add_argument('--edf-path', type=str, required=True,
                        help='Path to EDF file')
    parser.add_argument('--bootstrap-servers', type=str, 
                        default='localhost:9092,localhost:9094,localhost:9096',
                        help='Kafka bootstrap servers')
    parser.add_argument('--topic', type=str, default='eeg-signals',
                        help='Kafka topic name')
    parser.add_argument('--chunk-duration', type=int, default=1,
                        help='Chunk duration in seconds')
    
    args = parser.parse_args()
    
    # Kiểm tra file tồn tại
    if not os.path.exists(args.edf_path):
        logger.error(f"EDF file not found: {args.edf_path}")
        return
    
    producer = None
    try:
        # Khởi tạo producer
        producer = EEGKafkaProducer(
            bootstrap_servers=args.bootstrap_servers,
            topic=args.topic
        )
        
        # Xử lý và gửi dữ liệu
        total, successful = producer.process_and_send_edf_file(
            edf_path=args.edf_path,
            chunk_duration_sec=args.chunk_duration
        )
        
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        if producer:
            producer.close()


if __name__ == '__main__':
    main()
