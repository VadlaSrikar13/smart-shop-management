count = 0
File = open ('example.txt','r') 
data = file.read()
count+=len(data.split())
print(count)
File.close